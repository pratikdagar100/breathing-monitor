import time
import board
import busio
import adafruit_vl53l0x
import matplotlib.pyplot as plt
from collections import deque

# -------- SENSOR SETUP --------
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_vl53l0x.VL53L0X(i2c)

sensor.measurement_timing_budget = 20000
sensor.start_continuous()

# -------- GRAPH SETUP --------
plt.ion()
fig, ax = plt.subplots()

ax.set_title("Distance vs Time")
ax.set_xlabel("Samples")
ax.set_ylabel("Distance (mm)")

# 🔥 FIX: lock axis from 0 to 300 mm
ax.set_ylim(0, 300)

data = deque([0]*200, maxlen=200)
line, = ax.plot(data)

# -------- MAIN LOOP --------
last_time = time.time()

while True:
    distance = sensor.range
    data.append(distance)

    line.set_ydata(data)
    line.set_xdata(range(len(data)))

    # ❌ remove autoscaling (IMPORTANT)
    # ax.relim()
    # ax.autoscale_view()

    plt.draw()
    plt.pause(0.001)

    now = time.time()
    print(f"{distance} mm | {(now - last_time)*1000:.1f} ms")
    last_time = now

    time.sleep(0.005)
