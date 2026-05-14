import numpy as np
import wave
import RPi.GPIO as GPIO
import busio
import board
import adafruit_mpr121
import time
import threading

# ====================== [1. 하드웨어 초기화] ======================
i2c0 = busio.I2C(board.D1, board.D0)
i2c1 = busio.I2C(board.SCL, board.SDA)

mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)
mpr_ctrl = adafruit_mpr121.MPR121(i2c1, address=0x5A)

# ====================== [2. GPIO PWM 설정] ======================
GPIO.setmode(GPIO.BCM)
GPIO.setup(13, GPIO.OUT)
pwm = GPIO.PWM(13, 440)
pwm.start(0)

# ====================== [3. wav 파일 로드] ======================
SOUND_PATH = "/home/noisezero/noise_zero/piano/"

full_notes = [
    "AS1", "B1", "C2", "CS2", "D2", "DS2", "E2", "F2", "FS2", "G2", "GS2", "A2",
    "AS2", "B2", "C3", "CS3", "D3", "DS3", "E3", "F3", "FS3", "G3", "GS3", "A3",
    "AS3", "B3", "C4", "CS4", "D4", "DS4", "E4", "F4", "FS4", "G4", "GS4", "A4",
    "AS4", "B4", "C5", "CS5", "D5", "DS5"
]

piano_sounds = {}
for name in full_notes:
    path = SOUND_PATH + name + ".wav"
    if os.path.exists(path):
        with wave.open(path, 'r') as f:
            framerate = f.getframerate()
            frames = f.readframes(f.getnframes())
            data = np.frombuffer(frames, dtype=np.int16)
        piano_sounds[name] = (data, framerate)
        print(f"Loaded: {name}")
    else:
        print(f"Warning: {path} not found.")

# ====================== [4. 변수] ======================
base_index = 2
volume = 0.75
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0
is_playing = False

# ====================== [5. 함수] ======================
def play_wav(data, framerate):
    global is_playing
    is_playing = True
    for sample in data[::100]:
        if not is_playing:
            break
        freq = abs(int(sample)) + 1
        if freq > 20:
            pwm.ChangeFrequency(min(freq, 4000))
            pwm.ChangeDutyCycle(50)
        time.sleep(1/framerate * 100)
    pwm.ChangeDutyCycle(0)
    is_playing = False

def play_note(pad_idx, record=True):
    global loop_data, loop_start_time, is_playing
    target_idx = base_index + pad_idx
    if target_idx < len(full_notes):
        name = full_notes[target_idx]
        if name in piano_sounds:
            print(f"Playing: {name}")
            is_playing = False
            time.sleep(0.01)
            data, framerate = piano_sounds[name]
            threading.Thread(target=play_wav, args=(data, framerate), daemon=True).start()
            if loop_state == "RECORDING" and record:
                loop_data.append((time.time() - loop_start_time, target_idx))

def adjust_volume(delta):
    global volume
    volume = max(0.1, min(1.0, volume + delta))
    print(f"Volume: {int(volume*100)}%")

def handle_loop_logic():
    global loop_state, last_touch_time, loop_data, loop_start_time, is_looping
    now = time.time()

    if now - last_touch_time < 0.35:
        is_looping = False
        loop_data = []
        loop_state = "IDLE"
        print("LOOP RESET")
        return

    last_touch_time = now

    if loop_state == "IDLE":
        loop_state = "RECORDING"
        loop_data = []
        loop_start_time = time.time()
        print("RECORDING...")
    elif loop_state == "RECORDING":
        loop_state = "PLAYING"
        is_looping = True
        threading.Thread(target=loop_player, daemon=True).start()
        print("PLAYING LOOP")
    elif loop_state == "PLAYING":
        is_looping = False
        loop_state = "STOPPED"
        print("LOOP STOPPED")

def loop_player():
    global is_looping
    while is_looping:
        start = time.time()
        for ts, idx in loop_data:
            while time.time() - start < ts:
                if not is_looping:
                    return
                time.sleep(0.005)
            name = full_notes[idx]
            if name in piano_sounds:
                data, framerate = piano_sounds[name]
                threading.Thread(target=play_wav, args=(data, framerate), daemon=True).start()
        time.sleep(0.05)

# ====================== [6. 메인 루프] ======================
import os
print("=== Launchpad 시작 ===")

try:
    while True:
        if mpr_ctrl[0].value:
            base_index = min(len(full_notes)-12, base_index + 12)
            print("Oct UP")
            while mpr_ctrl[0].value: time.sleep(0.01)

        if mpr_ctrl[1].value:
            base_index = max(0, base_index - 12)
            print("Oct DOWN")
            while mpr_ctrl[1].value: time.sleep(0.01)

        if mpr_ctrl[2].value:
            handle_loop_logic()
            while mpr_ctrl[2].value: time.sleep(0.01)

        if mpr_ctrl[3].value:
            adjust_volume(0.05)
            while mpr_ctrl[3].value: time.sleep(0.01)

        if mpr_ctrl[4].value:
            adjust_volume(-0.05)
            while mpr_ctrl[4].value: time.sleep(0.01)

        for i in range(12):
            if mpr_piano[i].value:
                play_note(i)
                while mpr_piano[i].value:
                    time.sleep(0.012)

        time.sleep(0.008)

except KeyboardInterrupt:
    pwm.stop()
    GPIO.cleanup()
    print("종료!")
