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

# ====================== [3. 음계 주파수 설정] ======================
# 4옥타브 기준 전체 음계
all_notes = [
    ("AS1", 58),  ("B1", 62),   ("C2", 65),  ("CS2", 69),
    ("D2", 73),   ("DS2", 78),  ("E2", 82),  ("F2", 87),
    ("FS2", 93),  ("G2", 98),   ("GS2", 104),("A2", 110),
    ("AS2", 117), ("B2", 123),  ("C3", 131), ("CS3", 139),
    ("D3", 147),  ("DS3", 156), ("E3", 165), ("F3", 175),
    ("FS3", 185), ("G3", 196),  ("GS3", 208),("A3", 220),
    ("AS3", 233), ("B3", 247),  ("C4", 262), ("CS4", 277),
    ("D4", 294),  ("DS4", 311), ("E4", 330), ("F4", 349),
    ("FS4", 370), ("G4", 392),  ("GS4", 415),("A4", 440),
    ("AS4", 466), ("B4", 494),  ("C5", 523), ("CS5", 554),
    ("D5", 587),  ("DS5", 622)
]

# ====================== [4. 변수] ======================
base_index = 26  # 기본 C4부터 시작
volume = 50      # 듀티사이클 (0~100)
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

# ====================== [5. 함수] ======================
def play_note(pad_idx, record=True):
    global loop_data, loop_start_time
    target_idx = base_index + pad_idx
    if target_idx < len(all_notes):
        name, freq = all_notes[target_idx]
        print(f"Playing: {name} ({freq}Hz)")
        pwm.ChangeFrequency(freq)
        pwm.ChangeDutyCycle(volume)
        if loop_state == "RECORDING" and record:
            loop_data.append((time.time() - loop_start_time, target_idx))

def stop_note():
    pwm.ChangeDutyCycle(0)

def adjust_volume(delta):
    global volume
    volume = max(10, min(100, volume + delta))
    print(f"Volume: {volume}%")

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
            name, freq = all_notes[idx]
            pwm.ChangeFrequency(freq)
            pwm.ChangeDutyCycle(volume)
        time.sleep(0.05)

# ====================== [6. 메인 루프] ======================
print("=== Launchpad 시작 ===")

try:
    while True:
        if mpr_ctrl[0].value:
            base_index = min(len(all_notes)-12, base_index + 12)
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
            adjust_volume(5)
            while mpr_ctrl[3].value: time.sleep(0.01)

        if mpr_ctrl[4].value:
            adjust_volume(-5)
            while mpr_ctrl[4].value: time.sleep(0.01)

        for i in range(12):
            if mpr_piano[i].value:
                play_note(i)
                while mpr_piano[i].value:
                    time.sleep(0.012)
                stop_note()

        time.sleep(0.008)

except KeyboardInterrupt:
    pwm.stop()
    GPIO.cleanup()
    print("종료!")
