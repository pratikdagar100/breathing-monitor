import sys, csv, os
import numpy as np
from collections import deque
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtMultimedia import QSound
import pyqtgraph as pg

# SENSOR
import board, busio, adafruit_vl53l0x

# -------- SENSOR --------
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_vl53l0x.VL53L0X(i2c)
sensor.start_continuous()

# -------- TITLE BAR --------
class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)

        self.title = QtWidgets.QLabel("Smart Sensor System")
        self.title.setStyleSheet("color:white; font-size:16px;")

        btn_min = QtWidgets.QPushButton("-")
        btn_max = QtWidgets.QPushButton("⬜")
        btn_close = QtWidgets.QPushButton("X")

        btn_min.clicked.connect(parent.showMinimized)
        btn_max.clicked.connect(lambda: parent.showMaximized() if not parent.isMaximized() else parent.showNormal())
        btn_close.clicked.connect(parent.close)

        for b in [btn_min, btn_max, btn_close]:
            b.setFixedWidth(40)

        layout.addWidget(self.title)
        layout.addStretch()
        layout.addWidget(btn_min)
        layout.addWidget(btn_max)
        layout.addWidget(btn_close)

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

        p.setPen(QtGui.QPen(QtGui.QColor("#333"), 10))
        p.drawEllipse(r)

        angle = int((self.value / 500) * 270)
        p.setPen(QtGui.QPen(QtGui.QColor("cyan"), 10))
        p.drawArc(r, 135*16, -angle*16)

# -------- BREATHING --------
class BreathingWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        self.label = QtWidgets.QLabel("Breathe In")
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setStyleSheet("font-size:40px; color:cyan;")

        layout.addWidget(self.label)

        self.state = True
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_cycle)
        self.timer.start(2000)

    def update_cycle(self):
        if self.state:
            self.label.setText("Breathe Out")
            self.label.setStyleSheet("font-size:40px; color:orange;")
        else:
            self.label.setText("Breathe In")
            self.label.setStyleSheet("font-size:40px; color:cyan;")
        self.state = not self.state

# -------- MAIN APP --------
class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Dashboard")
        self.resize(800, 480)

        # STACK
        self.stack = QtWidgets.QStackedWidget()
        self.dashboard = QtWidgets.QWidget()
        self.breathing = BreathingWidget()

        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.breathing)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(TitleBar(self))
        main_layout.addWidget(self.stack)

        container = QtWidgets.QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.init_dashboard()

    def init_dashboard(self):
        layout = QtWidgets.QVBoxLayout(self.dashboard)

        title = QtWidgets.QLabel("SMART DISTANCE DASHBOARD")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size:24px; color:cyan;")
        layout.addWidget(title)

        # GRAPH
        self.graph = pg.PlotWidget()
        self.graph.setBackground('#0a0a0a')
        self.graph.setLabel('left', 'Distance (mm)')
        self.graph.setLabel('bottom', 'Samples')
        self.graph.showGrid(x=True, y=True)
        layout.addWidget(self.graph)

        self.data = deque([220]*100, maxlen=100)
        self.curve = self.graph.plot(pen=pg.mkPen('c', width=2))

        # GAUGE
        self.gauge = GaugeWidget()
        self.gauge.setFixedHeight(120)
        layout.addWidget(self.gauge)

        # VALUE
        self.value_label = QtWidgets.QLabel("0 mm")
        self.value_label.setAlignment(QtCore.Qt.AlignCenter)
        self.value_label.setStyleSheet("font-size:30px; color:lime;")
        layout.addWidget(self.value_label)

        # STATS
        stats = QtWidgets.QHBoxLayout()
        self.min_label = QtWidgets.QLabel("Min: 0")
        self.max_label = QtWidgets.QLabel("Max: 0")
        self.avg_label = QtWidgets.QLabel("Avg: 0")

        for l in [self.min_label, self.max_label, self.avg_label]:
            l.setStyleSheet("color:white;")
            stats.addWidget(l)

        layout.addLayout(stats)

        # SLIDERS
        sliders = QtWidgets.QHBoxLayout()

        self.range_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.range_slider.setRange(100, 500)
        self.range_slider.setValue(250)

        self.smooth_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.smooth_slider.setRange(1, 30)
        self.smooth_slider.setValue(5)

        sliders.addWidget(QtWidgets.QLabel("Range"))
        sliders.addWidget(self.range_slider)
        sliders.addWidget(QtWidgets.QLabel("Smooth"))
        sliders.addWidget(self.smooth_slider)

        layout.addLayout(sliders)

        # BUTTONS
        btns = QtWidgets.QHBoxLayout()

        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.reset_btn = QtWidgets.QPushButton("Reset")
        self.mode_btn = QtWidgets.QPushButton("Breathing Mode")

        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.reset_btn)
        btns.addWidget(self.mode_btn)

        layout.addLayout(btns)

        # ACTIONS
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)

        self.start_btn.clicked.connect(lambda: self.timer.start(20))
        self.stop_btn.clicked.connect(self.timer.stop)
        self.reset_btn.clicked.connect(lambda: self.data.clear())
        self.mode_btn.clicked.connect(self.switch_mode)

        self.setStyleSheet("QWidget{background:#000;} QPushButton{background:#111;color:white;padding:8px;}")

    def switch_mode(self):
        self.stack.setCurrentIndex(1 if self.stack.currentIndex()==0 else 0)

    def update(self):
        raw = sensor.range

        window = list(self.data)[-self.smooth_slider.value():]
        avg = int(np.mean(window)) if window else raw

        self.data.append(avg)

        x = np.arange(len(self.data))
        self.curve.setData(x, list(self.data))

        self.graph.setYRange(100, self.range_slider.value())

        self.value_label.setText(f"{avg} mm")
        self.gauge.setValue(avg)

        # COLOR
        color = "lime" if avg<180 else "yellow" if avg<300 else "red"
        self.value_label.setStyleSheet(f"font-size:30px; color:{color};")

        # STATS
        self.min_label.setText(f"Min: {min(self.data)}")
        self.max_label.setText(f"Max: {max(self.data)}")
        self.avg_label.setText(f"Avg: {int(np.mean(self.data))}")

        # LOG
        with open("log.csv","a") as f:
            csv.writer(f).writerow([avg])

        # ALERT
        if avg > 350:
            QSound.play("/usr/share/sounds/alsa/Front_Center.wav")

# -------- RUN --------
app = QtWidgets.QApplication(sys.argv)
win = App()
win.show()
sys.exit(app.exec_())
