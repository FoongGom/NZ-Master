python3 -c "
import numpy as np
import wave
import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setup(13, GPIO.OUT)
pwm = GPIO.PWM(13, 440)
pwm.start(0)

with wave.open('/home/noisezero/noise_zero/piano/C4.wav', 'r') as f:
    framerate = f.getframerate()
    frames = f.readframes(f.getnframes())
    data = np.frombuffer(frames, dtype=np.int16)

for sample in data[::100]:
    freq = abs(int(sample)) + 1
    if freq > 20:
        pwm.ChangeFrequency(min(freq, 4000))
        pwm.ChangeDutyCycle(50)
    time.sleep(1/framerate * 100)

pwm.stop()
GPIO.cleanup()
"
