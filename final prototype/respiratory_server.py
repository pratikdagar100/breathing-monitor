#!/usr/bin/env python3
"""
Respiratory Monitor Backend with WebSocket Streaming - BALANCED MODE
Sensor  : VL53L4CD
Features: Configurable smoothing for stable but responsive readings
"""

import time
import board
import busio
import adafruit_vl53l4cd
import numpy as np
from collections import deque
from flask import Flask, render_template, send_from_directory
from flask_socketio import SocketIO, emit
import eventlet
import threading

eventlet.monkey_patch()

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

    def calibrate_zero(self):
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

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('calibration_status', {'calibrated': sensor.calibrated})
    if sensor.reference_zero:
        emit('calibration_complete', {
            'zero_value': int(round(sensor.reference_zero)),
            'message': f"Sensor ready at {sensor.reference_zero:.0f} cm"
        })

@socketio.on('calibrate')
def handle_calibration():
    print("Starting calibration...")
    success = sensor.calibrate_zero()
    emit('calibration_status', {'calibrated': success})
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
            # Determine phase with thresholds
            if buffered_disp >= CONFIG['INHALE_THRESHOLD']:
                phase = "INHALE"
            elif buffered_disp <= -CONFIG['EXHALE_THRESHOLD']:
                phase = "EXHALE"
            else:
                phase = "PAUSE"
            
            # Send data
            socketio.emit('sensor_data', {
                'displacement': buffered_disp,
                'phase': phase,
                'stable': stable_count > 2,  # Indicator if reading is stable
                'timestamp': current_time
            })
            last_send = current_time
            frame_count += 1
        
        # Debug output every 2 seconds
        if current_time - last_debug >= 2.0:
            quality = getattr(sensor, 'connection_quality', 1.0)
            print(f"[Monitor] Disp: {buffered_disp:+d} cm | "
                  f"Phase: {phase} | Rate: {frame_count/2:.1f} Hz | "
                  f"Quality: {quality:.0%}")
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
    print("=" * 50 + "\n")
    socketio.start_background_task(balanced_stream)

@socketio.on('stop_streaming')
def handle_stop_streaming():
    global streaming
    streaming = False
    print("\nStreaming stopped\n")

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
    print(" Stable readings with good responsiveness")
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
    print("\nTIPS FOR BEST RESULTS:")
    print("• Position sensor 15-30cm from chest")
    print("• Calibrate with person breathing normally")
    print("• For faster response: Decrease FILTER_ALPHA (currently 0.35)")
    print("• For smoother data: Increase FILTER_ALPHA to 0.5-0.6")
    print("=" * 60 + "\n")
    
    socketio.run(app, 
                 host='0.0.0.0', 
                 port=5000, 
                 debug=False,
                 use_reloader=False)
