import time
import board
import busio
import adafruit_vl53l0x

# Init I2C
i2c = busio.I2C(board.SCL, board.SDA)

# Init sensor
sensor = adafruit_vl53l0x.VL53L0X(i2c)

# Reduce timing budget (lower = faster, but noisier)
sensor.measurement_timing_budget = 20000   # 20 ms

# Start continuous measurement (IMPORTANT)
sensor.start_continuous()

print("Starting fast read...")

last_time = time.time()

while True:
    distance = sensor.range  # read instantly

    now = time.time()
    dt = (now - last_time) * 1000  # ms
    last_time = now

    print(f"{distance} mm | {dt:.2f} ms")

    # VERY small delay to avoid CPU hog (can go to 0.005)
    time.sleep(0.005)
