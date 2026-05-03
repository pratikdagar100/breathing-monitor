import time
import board
import busio
import adafruit_vl53l0x
import matplotlib.pyplot as plt
from collections import deque
import numpy as np
from scipy.interpolate import make_interp_spline

# -------- SENSOR --------
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_vl53l0x.VL53L0X(i2c)

sensor.measurement_timing_budget = 20000
sensor.start_continuous()

# -------- GRAPH --------
plt.ion()
fig, ax = plt.subplots()

ax.set_title("Ultra Smooth Curve")
ax.set_xlabel("Samples")
ax.set_ylabel("Distance (mm)")
ax.set_ylim(200, 250)
ax.grid(True)

data = deque([220]*100, maxlen=100)
line, = ax.plot(data)

# -------- FILTER --------
WINDOW_SIZE = 20
DEADBAND = 2.0

window = deque(maxlen=WINDOW_SIZE)
last_output = 220

# -------- MAIN LOOP --------
while True:
    raw = sensor.range
    window.append(raw)

    avg = np.mean(window)

    # deadband
    if abs(avg - last_output) < DEADBAND:
        output = last_output
    else:
        output = avg
        last_output = output

    data.append(output)

    # -------- INTERPOLATION --------
    y = np.array(data)
    x = np.arange(len(y))

    if len(y) > 5:
        x_new = np.linspace(x.min(), x.max(), 300)
        spline = make_interp_spline(x, y)
        y_smooth = spline(x_new)

        line.set_xdata(x_new)
        line.set_ydata(y_smooth)

    plt.draw()
    plt.pause(0.001)

    print(f"RAW: {raw} | OUTPUT: {output:.2f}")

    time.sleep(0.001)
