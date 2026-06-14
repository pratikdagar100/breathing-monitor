#!/usr/bin/env python3
"""
Respiratory Monitor Backend with WebSocket Streaming - BALANCED MODE
Sensor  : VL53L4CD
Features: Configurable smoothing for stable but responsive readings
          Patient data capture and session logging
"""

import time
import RPi.GPIO as GPIO
import board
import busio
import adafruit_vl53l4cd
import numpy as np
from collections import deque
from flask import Flask, render_template, send_from_directory, request, jsonify
from flask_socketio import SocketIO, emit
import eventlet
import threading
import json
import os
import uuid
from datetime import datetime

eventlet.monkey_patch()

# ----------------------------
# Data Storage Directory
# ----------------------------
DATA_DIR = "patient_sessions"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ----------------------------
# Relay GPIO Setup
# ----------------------------
RELAY_PIN = 17

GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.output(RELAY_PIN, GPIO.HIGH)   # Relay OFF initially

# ----------------------------
# CONFIGURATION - TUNE THESE FOR YOUR NEEDS
# ----------------------------
CONFIG = {
    # Speed vs Stability (0.1 = very stable/slow, 0.9 = very fast/noisy)
    'FILTER_ALPHA': 0.35,        # Lower = smoother but slower response
    
    # Window size for median filter (odd number, 3-9)
    'FILTER_WINDOW': 5,           # Larger = smoother but more lag
    
    # Time between sensor readings (seconds)
    'READ_INTERVAL': 0.05,        # 50ms between reads
    
    # Time between sending data to client (seconds)  
    'SEND_INTERVAL': 0.05,        # 50ms = 20Hz updates
    
    # Movement detection thresholds (cm)
    'INHALE_THRESHOLD': 0.8,      # Minimum cm to register as inhale
    'EXHALE_THRESHOLD': 0.8,      # Minimum cm to register as exhale
    
    # Displacement limits (cm)
    'MAX_DISPLACEMENT': 4.0,      # Maximum chest movement range
    
    # Calibration samples
    'CALIBRATION_SAMPLES': 5,
}

# ----------------------------
# Session Manager for Patient Data
# ----------------------------
class SessionManager:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.active_sessions = {}  # session_id -> session data
    
    def create_session(self, patient_info):
        """Create a new patient session"""
        session_id = str(uuid.uuid4())[:8]
        session = {
            'session_id': session_id,
            'patient': patient_info,
            'start_time': datetime.now().isoformat(),
            'data': [],
            'zero_value': None,
            'status': 'active'
        }
        self.active_sessions[session_id] = session
        return session_id, session
    
    def add_data_point(self, session_id, data_point):
        """Add a data point to the session"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id]['data'].append(data_point)
            return True
        return False
    
    def set_zero_value(self, session_id, zero_value):
        """Set the calibration zero value for the session"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id]['zero_value'] = zero_value
            return True
        return False
    
    def save_session(self, session_id):
        """Save session to JSON file"""
        if session_id not in self.active_sessions:
            return None
        
        session = self.active_sessions[session_id]
        session['end_time'] = datetime.now().isoformat()
        session['status'] = 'completed'
        
        # Generate filename
        patient_name = session['patient'].get('name', 'unknown').replace(' ', '_')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{patient_name}_{timestamp}_{session_id}.json"
        filepath = os.path.join(self.data_dir, filename)
        
        # Save to file
        with open(filepath, 'w') as f:
            json.dump(session, f, indent=2)
        
        # Remove from active sessions
        del self.active_sessions[session_id]
        
        return filename
    
    def get_all_sessions(self):
        """Get list of all saved sessions"""
        sessions = []
        for filename in os.listdir(self.data_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.data_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        session = json.load(f)
                        sessions.append({
                            'filename': filename,
                            'patient': session.get('patient', {}),
                            'date': session.get('start_time', ''),
                            'data_points': len(session.get('data', [])),
                            'zero_value': session.get('zero_value')
                        })
                except Exception as e:
                    print(f"Error reading {filename}: {e}")
        
        # Sort by date (newest first)
        sessions.sort(key=lambda x: x['date'], reverse=True)
        return sessions
    
    def load_session(self, filename):
        """Load a specific session by filename"""
        filepath = os.path.join(self.data_dir, filename)
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {filename}: {e}")
            return None

session_manager = SessionManager()

# ----------------------------
# Relay State Manager
# ----------------------------
class RelayManager:
    """Manages relay state with manual and auto modes - INVERTED LOGIC"""
    def __init__(self, pin):
        self.pin = pin
        self.mode = 'auto'  # 'auto' or 'manual'
        self.relay_on = False  # This represents the desired state (True = light ON)
        self.timer_active = False
        self.lock = threading.Lock()
        
    def _set_relay_pin(self, desired_state):
        """Set the actual GPIO pin based on desired state (inverted logic)"""
        if desired_state:  # Want relay ON (light on)
            GPIO.output(self.pin, GPIO.LOW)   # LOW = relay ON for active-low modules
        else:  # Want relay OFF (light off)
            GPIO.output(self.pin, GPIO.HIGH)  # HIGH = relay OFF for active-low modules
    
    def set_mode(self, mode):
        """Set relay mode: 'auto' or 'manual'"""
        with self.lock:
            self.mode = mode
            print(f"Relay mode set to: {mode}")
            # When switching to manual mode, ensure relay is OFF if not desired
            if mode == 'manual' and not self.relay_on:
                self._set_relay_pin(False)
            # Broadcast status update
            socketio.emit('relay_status_update', {
                'mode': self.mode,
                'state': self.relay_on,
                'timer_active': self.timer_active
            })
    
    def set_timer_state(self, active):
        """Called when timer starts/stops"""
        with self.lock:
            self.timer_active = active
            if self.mode == 'auto':
                # In auto mode, relay follows timer state
                if active:
                    self.relay_on = True
                    self._set_relay_pin(True)
                    print("🔴 Relay ON (Auto mode - timer active) - Light should be ON")
                else:
                    self.relay_on = False
                    self._set_relay_pin(False)
                    print("⚪ Relay OFF (Auto mode - timer stopped) - Light should be OFF")
                
                # Broadcast status update
                socketio.emit('relay_status_update', {
                    'mode': self.mode,
                    'state': self.relay_on,
                    'timer_active': self.timer_active,
                    'source': 'auto_timer'
                })
    
    def manual_control(self, turn_on):
        """Manual relay control from frontend"""
        with self.lock:
            if self.mode != 'manual':
                print(f"Ignoring manual control - mode is {self.mode}")
                return False
            
            self.relay_on = turn_on
            if turn_on:
                self._set_relay_pin(True)
                print("🔴 Relay ON (Manual mode) - Light should be ON")
            else:
                self._set_relay_pin(False)
                print("⚪ Relay OFF (Manual mode) - Light should be OFF")
            
            socketio.emit('relay_status_update', {
                'mode': self.mode,
                'state': self.relay_on,
                'timer_active': self.timer_active,
                'source': 'manual'
            })
            return True
    
    def get_status(self):
        """Get current relay status"""
        with self.lock:
            return {
                'mode': self.mode,
                'state': self.relay_on,
                'timer_active': self.timer_active,
                'pin': self.pin
            }

# Initialize relay manager
relay_manager = RelayManager(RELAY_PIN)

# ----------------------------
# Adaptive Noise Filter Class
# ----------------------------
class AdaptiveNoiseFilter:
    """
    Filter that adapts to movement: 
    - Fast response for large movements
    - Smooth for small/no movements
    """
    def __init__(self, base_alpha=0.35, min_alpha=0.2, max_alpha=0.7):
        self.base_alpha = base_alpha
        self.min_alpha = min_alpha
        self.max_alpha = max_alpha
        self.window_size = CONFIG['FILTER_WINDOW']
        self.buffer = deque(maxlen=self.window_size)
        self.filtered_value = None
        self.last_raw = None
        
    def apply(self, raw_value):
        self.buffer.append(raw_value)
        median_val = np.median(self.buffer)
        
        # Calculate movement speed to adapt filter response
        if self.last_raw is not None:
            movement_speed = abs(raw_value - self.last_raw)
            # Fast movement = less smoothing (higher alpha)
            adaptive_alpha = min(self.max_alpha, 
                                self.base_alpha + movement_speed * 0.15)
            adaptive_alpha = max(self.min_alpha, adaptive_alpha)
        else:
            adaptive_alpha = self.base_alpha
            
        self.last_raw = raw_value
        
        if self.filtered_value is None:
            self.filtered_value = median_val
        else:
            self.filtered_value = adaptive_alpha * median_val + \
                                 (1 - adaptive_alpha) * self.filtered_value
        
        return self.filtered_value

# ----------------------------
# Sensor Reader with Balanced Performance
# ----------------------------
class RespiratorySensor:
    def __init__(self):
        self.sensor = None
        self.filter = None
        self.reference_zero = None
        self.calibrated = False
        self.last_reading = 0
        self.reading_lock = threading.Lock()
        self.current_session_id = None  # Track current session for data logging
        
        # For stability tracking
        self.reading_history = deque(maxlen=10)
        self.connection_quality = 1.0
        
        try:
            self.i2c = busio.I2C(board.SCL, board.SDA)
            self.sensor = adafruit_vl53l4cd.VL53L4CD(self.i2c)
            self.sensor.start_ranging()
            self.filter = AdaptiveNoiseFilter(base_alpha=CONFIG['FILTER_ALPHA'])
            print("✓ VL53L4CD sensor initialized")
            print(f"  Filter alpha: {CONFIG['FILTER_ALPHA']}")
            print(f"  Window size: {CONFIG['FILTER_WINDOW']}")
            print(f"  Read interval: {CONFIG['READ_INTERVAL']*1000:.0f}ms")
        except Exception as e:
            print(f"✗ Sensor init error: {e}")

    def _read_one_cm(self, timeout=0.05):
        """Read one distance measurement in cm"""
        start_time = time.time()
        attempts = 0
        
        while time.time() - start_time < timeout:
            try:
                if self.sensor and self.sensor.data_ready:
                    dist_cm = self.sensor.distance
                    self.sensor.clear_interrupt()
                    if dist_cm is not None and 5 < dist_cm < 200:  # Valid range
                        # Track reading quality
                        self.reading_history.append(dist_cm)
                        if len(self.reading_history) > 3:
                            # Check consistency of last 3 readings
                            std_dev = np.std(list(self.reading_history)[-3:])
                            self.connection_quality = max(0.5, min(1.0, 
                                                             1.0 - std_dev / 20.0))
                        return dist_cm
            except Exception:
                pass
            attempts += 1
            time.sleep(0.002)  # 2ms sleep between checks
        
        # Return last valid reading if timeout (better than 0)
        return self.last_reading if self.last_reading != 0 else None

    def set_session(self, session_id):
        """Set the current session for data logging"""
        self.current_session_id = session_id

    def calibrate_zero(self, session_id=None):
        """Calibrate baseline distance - uses median of samples"""
        if not self.sensor:
            return False

        print("\n" + "=" * 50)
        print("CALIBRATION - Please remain still")
        print("=" * 50)

        samples_cm = []
        samples_needed = CONFIG['CALIBRATION_SAMPLES']
        
        print(f"Collecting {samples_needed} samples...")
        
        for i in range(samples_needed):
            dist = self._read_one_cm(timeout=0.1)
            if dist is not None and dist > 10:  # Valid distance
                samples_cm.append(dist)
                print(f"  [{i+1}/{samples_needed}] {dist:.1f} cm")
            else:
                print(f"  [{i+1}/{samples_needed}] Invalid reading, retrying...")
                # Try one more time for this sample
                dist = self._read_one_cm(timeout=0.1)
                if dist is not None and dist > 10:
                    samples_cm.append(dist)
                    print(f"  [{i+1}/{samples_needed}] {dist:.1f} cm (retry)")
            time.sleep(0.1)

        if len(samples_cm) >= 3:
            # Use median to ignore outliers
            self.reference_zero = float(np.median(samples_cm))
            self.calibrated = True
            # Reset filter after calibration
            self.filter = AdaptiveNoiseFilter(base_alpha=CONFIG['FILTER_ALPHA'])
            
            # Store zero value in session if provided
            if session_id:
                session_manager.set_zero_value(session_id, self.reference_zero)
            
            print(f"\n✓ Calibration successful!")
            print(f"  Baseline distance: {self.reference_zero:.1f} cm")
            print(f"  Samples used: {len(samples_cm)}")
            print(f"  Range: {min(samples_cm):.1f} - {max(samples_cm):.1f} cm")
            print("=" * 50 + "\n")
            return True
        else:
            print(f"\n✗ Calibration failed! Only {len(samples_cm)} valid samples.")
            print("  Ensure sensor is properly positioned (10-50cm from chest)")
            print("=" * 50 + "\n")
            return False

    def get_displacement(self):
        """Get relative displacement with balanced filtering"""
        if not self.sensor or not self.calibrated:
            return 0

        with self.reading_lock:
            # Get raw reading
            dist_cm = self._read_one_cm(timeout=CONFIG['READ_INTERVAL'])
            
            if dist_cm is None or dist_cm <= 0:
                return self.last_reading  # Return last good reading
            
            try:
                # Apply adaptive filter
                filtered_cm = self.filter.apply(dist_cm)
                
                # Calculate displacement (positive = towards sensor = inhale)
                displacement_cm = self.reference_zero - filtered_cm
                
                # Apply movement thresholds for stability
                abs_disp = abs(displacement_cm)
                if abs_disp < 0.3:  # Very small movement - treat as pause
                    displacement_cm = 0
                elif abs_disp < 0.6:  # Small movement - reduce sensitivity
                    displacement_cm = displacement_cm * 0.7
                
                # Clamp to reasonable range
                displacement_cm = max(-CONFIG['MAX_DISPLACEMENT'], 
                                     min(CONFIG['MAX_DISPLACEMENT'], 
                                         displacement_cm))
                
                # Round to 1 decimal for stability, then to int
                self.last_reading = int(round(displacement_cm, 0))
                return self.last_reading
                
            except Exception as e:
                print(f"Displacement error: {e}")
                return self.last_reading

# ----------------------------
# Flask App with Optimized SocketIO
# ----------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'respiratory_monitor_secret'
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='eventlet',
                    ping_timeout=15,
                    ping_interval=5)

sensor = RespiratorySensor()
streaming = False

# For smoothing the data stream
class DataBuffer:
    def __init__(self, buffer_size=3):
        self.buffer = deque(maxlen=buffer_size)
        
    def add(self, value):
        self.buffer.append(value)
        if len(self.buffer) == self.buffer.maxlen:
            # Send median of buffer for extra stability
            return int(round(np.median(self.buffer)))
        return value

data_buffer = DataBuffer(buffer_size=3)

# ----------------------------
# API Routes for Patient Data
# ----------------------------
@app.route('/api/session/start', methods=['POST'])
def api_session_start():
    """Start a new patient session"""
    try:
        patient_info = request.json
        session_id, session = session_manager.create_session(patient_info)
        sensor.set_session(session_id)
        return jsonify({
            'success': True,
            'sessionId': session_id,
            'patientInfo': session['patient'],
            'message': f"Session started for {patient_info.get('name', 'Patient')}"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/session/save', methods=['POST'])
def api_session_save():
    """Save the current session"""
    try:
        data = request.json
        session_id = data.get('sessionId')
        
        # Add all data points to the session
        for point in data.get('dataPoints', []):
            session_manager.add_data_point(session_id, point)
        
        # Set zero value if provided
        if data.get('zeroValue'):
            session_manager.set_zero_value(session_id, data['zeroValue'])
        
        filename = session_manager.save_session(session_id)
        
        if filename:
            return jsonify({
                'success': True,
                'filename': filename,
                'message': f"Session saved to {filename}"
            })
        else:
            return jsonify({'success': False, 'error': 'Session not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/list')
def api_sessions_list():
    """Get list of all saved sessions"""
    try:
        sessions = session_manager.get_all_sessions()
        return jsonify(sessions)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/session/view/<filename>')
def api_session_view(filename):
    """View a specific session"""
    try:
        session = session_manager.load_session(filename)
        if session:
            return jsonify({'success': True, 'session': session})
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

# ----------------------------
# Socket.IO Handlers
# ----------------------------
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('calibration_status', {'calibrated': sensor.calibrated, 'zero_value': sensor.reference_zero})
    if sensor.reference_zero:
        emit('calibration_complete', {
            'zero_value': int(round(sensor.reference_zero)),
            'message': f"Sensor ready at {sensor.reference_zero:.0f} cm"
        })
    # Send current relay status on connect
    emit('relay_status_update', relay_manager.get_status())

@socketio.on('calibrate')
def handle_calibration():
    print("Starting calibration...")
    # Get current session ID from sensor
    session_id = sensor.current_session_id
    success = sensor.calibrate_zero(session_id=session_id)
    emit('calibration_status', {'calibrated': success, 'zero_value': sensor.reference_zero})
    if success:
        zero_cm = int(round(sensor.reference_zero))
        emit('calibration_complete', {
            'zero_value': zero_cm,
            'message': f"Calibrated! Baseline: {zero_cm} cm"
        })
    return success

def balanced_stream():
    """Stream with balanced speed and stability"""
    global streaming
    last_send = 0
    last_debug = 0
    frame_count = 0
    last_displacement = 0
    stable_count = 0
    
    while streaming:
        current_time = time.time()
        
        # Get displacement
        displacement = sensor.get_displacement()
        
        # Apply output buffering for extra stability
        buffered_disp = data_buffer.add(displacement)
        
        # Check if value is stable (not oscillating)
        if buffered_disp == last_displacement:
            stable_count += 1
        else:
            stable_count = 0
            last_displacement = buffered_disp
        
        # Throttle sending to configured interval
        if current_time - last_send >= CONFIG['SEND_INTERVAL']:
            # Determine phase with thresholds (for display only)
            if buffered_disp >= CONFIG['INHALE_THRESHOLD']:
                phase = "INHALE"
            elif buffered_disp <= -CONFIG['EXHALE_THRESHOLD']:
                phase = "EXHALE"
            else:
                phase = "PAUSE"
            
            # Send data (relay state is now managed by timer, not directly by phase)
            socketio.emit('sensor_data', {
                'displacement': buffered_disp,
                'phase': phase,
                'stable': stable_count > 2,
                'timestamp': current_time,
                'relay_state': relay_manager.relay_on  # Send current relay state
            })
            last_send = current_time
            frame_count += 1
        
        # Debug output every 2 seconds
        if current_time - last_debug >= 2.0:
            quality = getattr(sensor, 'connection_quality', 1.0)
            status = relay_manager.get_status()
            session_info = f" | Session: {sensor.current_session_id}" if sensor.current_session_id else ""
            print(f"[Monitor] Disp: {buffered_disp:+d} cm | "
                  f"Phase: {phase} | Rate: {frame_count/2:.1f} Hz | "
                  f"Quality: {quality:.0%} | "
                  f"Relay: {'ON' if status['state'] else 'OFF'} ({status['mode']} mode){session_info}")
            last_debug = current_time
            frame_count = 0
        
        # Small sleep to prevent CPU overload
        time.sleep(0.01)

@socketio.on('start_streaming')
def handle_start_streaming():
    global streaming
    streaming = True
    print("\n" + "=" * 50)
    print("BALANCED MODE ACTIVE")
    print(f"  Filter alpha: {CONFIG['FILTER_ALPHA']}")
    print(f"  Update rate: {int(1/CONFIG['SEND_INTERVAL'])} Hz")
    print(f"  Thresholds: Inhale>{CONFIG['INHALE_THRESHOLD']}cm, Exhale<{-CONFIG['EXHALE_THRESHOLD']}cm")
    if sensor.current_session_id:
        print(f"  Active session: {sensor.current_session_id}")
    print("=" * 50 + "\n")
    socketio.start_background_task(balanced_stream)

@socketio.on('stop_streaming')
def handle_stop_streaming():
    global streaming
    streaming = False
    print("\nStreaming stopped\n")

@socketio.on('relay_control')
def handle_relay_control(data):
    """Handle manual relay control from frontend"""
    try:
        state = data.get('state', False)
        success = relay_manager.manual_control(state)
        return {'success': success}
    except Exception as e:
        print(f"Error controlling relay: {e}")
        return {'success': False, 'error': str(e)}

@socketio.on('relay_set_mode')
def handle_relay_set_mode(data):
    """Set relay operation mode (auto/manual)"""
    try:
        mode = data.get('mode', 'auto')
        if mode in ['auto', 'manual']:
            relay_manager.set_mode(mode)
            return {'success': True, 'mode': mode}
        return {'success': False, 'error': 'Invalid mode'}
    except Exception as e:
        print(f"Error setting relay mode: {e}")
        return {'success': False, 'error': str(e)}

@socketio.on('relay_timer_state')
def handle_relay_timer_state(data):
    """Receive timer state from frontend to control relay"""
    try:
        timer_active = data.get('active', False)
        print(f"Timer state update: {'ACTIVE' if timer_active else 'INACTIVE'}")
        relay_manager.set_timer_state(timer_active)
        return {'success': True}
    except Exception as e:
        print(f"Error updating timer state: {e}")
        return {'success': False, 'error': str(e)}

@socketio.on('get_relay_status')
def handle_get_relay_status():
    """Get current relay status"""
    emit('relay_status_update', relay_manager.get_status())

@socketio.on('update_config')
def handle_update_config(config_updates):
    """Allow frontend to adjust settings dynamically"""
    for key, value in config_updates.items():
        if key in CONFIG:
            CONFIG[key] = value
            print(f"Config updated: {key} = {value}")
    
    # Reinitialize filter if alpha changed
    if 'FILTER_ALPHA' in config_updates or 'FILTER_WINDOW' in config_updates:
        sensor.filter = AdaptiveNoiseFilter(base_alpha=CONFIG['FILTER_ALPHA'])
        sensor.filter.window_size = CONFIG['FILTER_WINDOW']
        sensor.filter.buffer = deque(maxlen=CONFIG['FILTER_WINDOW'])
    
    emit('config_updated', CONFIG)

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print(" RESPIRATORY MONITOR - BALANCED MODE")
    print(" With Patient Data Capture & Session Logging")
    print("=" * 60)
    
    if sensor.sensor:
        print("✓ Sensor detected")
        # Quick test reading
        test_reading = sensor._read_one_cm(timeout=0.2)
        if test_reading:
            print(f"  Test reading: {test_reading:.1f} cm")
        else:
            print("  ⚠ No reading yet - ensure sensor is positioned correctly")
    else:
        print("⚠ Sensor not detected! Check wiring:")
        print("  SDA → GPIO2, SCL → GPIO3, VIN → 3.3V, GND → GND")
    
    print("\n" + "=" * 60)
    print(" Access at: http://localhost:5000")
    print(" or http://<YOUR_PI_IP>:5000")
    print("=" * 60)
    print("\nFEATURES:")
    print("• Patient data capture (name, age, gender, phone, notes)")
    print("• Session logging to JSON files")
    print("• View past sessions from the dashboard")
    print("• Auto-save on page unload")
    print("\nTIPS FOR BEST RESULTS:")
    print("• Position sensor 15-30cm from chest")
    print("• Calibrate with person breathing normally")
    print("• Each calibration starts a new patient session")
    print("\nRELAY CONTROL:")
    print("• Auto mode: Relay follows timer state from frontend")
    print("• Manual mode: Use button to manually control relay")
    print("=" * 60 + "\n")
    
    try:
        socketio.run(app, 
                     host='0.0.0.0', 
                     port=5000, 
                     debug=False,
                     use_reloader=False)
    finally:
        GPIO.cleanup()
