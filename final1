import sys, csv
import numpy as np
from collections import deque
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtMultimedia import QSound
import pyqtgraph as pg

# SENSOR
import board, busio, adafruit_vl53l0x

# -------- SENSOR SETUP --------
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_vl53l0x.VL53L0X(i2c)
sensor.start_continuous()

# -------- GAUGE --------
class GaugeWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0

    def setValue(self, v):
        self.value = v
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        r = self.rect().adjusted(10,10,-10,-10)

        # background
        p.setPen(QtGui.QPen(QtGui.QColor("#222"), 10))
        p.drawEllipse(r)

        # arc
        angle = int((self.value / 500) * 270)
        color = QtGui.QColor("lime") if self.value < 180 else QtGui.QColor("yellow") if self.value < 300 else QtGui.QColor("red")

        p.setPen(QtGui.QPen(color, 10))
        p.drawArc(r, 135*16, -angle*16)

# -------- MAIN APP --------
class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Smart Distance Dashboard")
        self.resize(800, 480)

        main = QtWidgets.QWidget()
        self.setCentralWidget(main)
        layout = QtWidgets.QVBoxLayout(main)

        # -------- TITLE --------
        title = QtWidgets.QLabel("SMART DISTANCE DASHBOARD")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size:22px; color:cyan; font-weight:bold;")
        layout.addWidget(title)

        # -------- GRAPH --------
        self.graph = pg.PlotWidget()
        self.graph.setBackground('#0a0a0a')
        self.graph.setLabel('left', 'Distance (mm)')
        self.graph.setLabel('bottom', 'Samples')
        self.graph.showGrid(x=True, y=True)
        self.graph.setYRange(100, 400)
        layout.addWidget(self.graph)

        self.data = deque([220]*100, maxlen=100)
        self.curve = self.graph.plot(pen=pg.mkPen('c', width=2))

        # -------- GAUGE + VALUE --------
        mid_layout = QtWidgets.QHBoxLayout()

        self.gauge = GaugeWidget()
        self.gauge.setFixedSize(150,150)

        self.value_label = QtWidgets.QLabel("0 mm")
        self.value_label.setAlignment(QtCore.Qt.AlignCenter)
        self.value_label.setStyleSheet("font-size:28px; color:lime;")

        mid_layout.addWidget(self.gauge)
        mid_layout.addWidget(self.value_label)

        layout.addLayout(mid_layout)

        # -------- STATS --------
        stats = QtWidgets.QHBoxLayout()

        self.min_label = QtWidgets.QLabel("Min: 0")
        self.max_label = QtWidgets.QLabel("Max: 0")
        self.avg_label = QtWidgets.QLabel("Avg: 0")

        for l in [self.min_label, self.max_label, self.avg_label]:
            l.setStyleSheet("color:white; font-size:14px;")
            stats.addWidget(l)

        layout.addLayout(stats)

        # -------- CONTROLS --------
        controls = QtWidgets.QHBoxLayout()

        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.reset_btn = QtWidgets.QPushButton("Reset")

        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.reset_btn)

        layout.addLayout(controls)

        # -------- TIMER --------
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)

        self.start_btn.clicked.connect(lambda: self.timer.start(50))  # slower = smoother
        self.stop_btn.clicked.connect(self.timer.stop)
        self.reset_btn.clicked.connect(self.reset)

        # -------- DATA --------
        self.smooth_buffer = deque(maxlen=30)
        self.log_counter = 0
        self.alerted = False

        # -------- STYLE --------
        self.setStyleSheet("""
            QWidget { background:#000; }
            QPushButton {
                background:#111;
                color:white;
                padding:8px;
                border-radius:6px;
            }
            QPushButton:hover { background:#333; }
        """)

    def reset(self):
        self.data = deque([220]*100, maxlen=100)
        self.smooth_buffer.clear()

    def update(self):
        raw = sensor.range

        # -------- SMOOTHING --------
        self.smooth_buffer.append(raw)
        avg = int(np.mean(self.smooth_buffer))

        self.data.append(avg)

        x = np.arange(len(self.data))
        self.curve.setData(x, list(self.data), antialias=True)

        # -------- UI UPDATE --------
        self.value_label.setText(f"{avg} mm")
        self.gauge.setValue(avg)

        color = "lime" if avg<180 else "yellow" if avg<300 else "red"
        self.value_label.setStyleSheet(f"font-size:28px; color:{color};")

        self.min_label.setText(f"Min: {min(self.data)}")
        self.max_label.setText(f"Max: {max(self.data)}")
        self.avg_label.setText(f"Avg: {int(np.mean(self.data))}")

        # -------- LOG (optimized) --------
        self.log_counter += 1
        if self.log_counter % 20 == 0:
            with open("log.csv","a") as f:
                csv.writer(f).writerow([avg])

        # -------- ALERT --------
        if avg > 350 and not self.alerted:
            QSound.play("/usr/share/sounds/alsa/Front_Center.wav")
            self.alerted = True
        if avg <= 350:
            self.alerted = False


# -------- RUN --------
app = QtWidgets.QApplication(sys.argv)
win = App()
win.showFullScreen()   # perfect for 7-inch display
sys.exit(app.exec_())
