import time
import board
import busio
import adafruit_mpr121
from pygame import mixer
import os
import threading

# ====================== [1. 하드웨어 초기화] ======================
i2c0 = busio.I2C(board.D1, board.D0)      # Sensor1: 12개 연주 패드
i2c1 = busio.I2C(board.SCL, board.SDA)    # Sensor2: 컨트롤

mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)
mpr_ctrl = adafruit_mpr121.MPR121(i2c1, address=0x5A)

# ====================== [2. 오디오 설정] ======================
os.environ["SDL_AUDIODRIVER"] = "alsa"
os.environ["AUDIODEV"] = "hw:2,0"
mixer.pre_init(44100, -16, 2, 1024)
mixer.init()

print("🎵 오디오 출력 시작")

SOUND_PATH = "/home/noisezero/noise_zero/piano/"

full_notes = [
    "AS1", "B1", "C2", "CS2", "D2", "DS2", "E2", "F2", "FS2", "G2", "GS2", "A2",
    "AS2", "B2", "C3", "CS3", "D3", "DS3", "E3", "F3", "FS3", "G3", "GS3", "A3",
    "AS3", "B3", "C4", "CS4", "D4", "DS4", "E4", "F4", "FS4", "G4", "GS4", "A4",
    "AS4", "B4", "C5", "CS5", "D5", "DS5"
]

piano_sounds = []
for name in full_notes:
    path = os.path.join(SOUND_PATH, f"{name}.wav")
    if os.path.exists(path):
        piano_sounds.append(mixer.Sound(path))
    else:
        print(f"Warning: {path} not found.")

# ====================== [3. 변수] ======================
base_index = 2
volume = 0.75
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

for sound in piano_sounds:
    sound.set_volume(volume)

# ====================== [4. 함수] ======================
def adjust_volume(delta):
    global volume
    volume = max(0.1, min(1.0, volume + delta))
    for sound in piano_sounds:
        sound.set_volume(volume)
    print(f"Volume: {int(volume*100)}%")

def play_note(pad_idx, record=True):
    global loop_data, loop_start_time
    target_idx = base_index + pad_idx
    if target_idx < len(piano_sounds):
        print(f"Playing: {full_notes[target_idx]}")
        piano_sounds[target_idx].play()
        if loop_state == "RECORDING" and record:
            loop_data.append((time.time() - loop_start_time, target_idx))

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
                if not is_looping: return
                time.sleep(0.005)
            piano_sounds[idx].play()
        time.sleep(0.05)

# ====================== [5. 메인 루프] ======================
print("=== Launchpad 시작 ===")

while True:
    # Sensor2 컨트롤
    if mpr_ctrl[0].value:   # 옥타브 업
        base_index = min(len(piano_sounds)-12, base_index + 12)
        print("Oct UP")
        while mpr_ctrl[0].value: time.sleep(0.01)

    if mpr_ctrl[1].value:   # 옥타브 다운
        base_index = max(0, base_index - 12)
        print("Oct DOWN")
        while mpr_ctrl[1].value: time.sleep(0.01)

    if mpr_ctrl[2].value:   # 루프
        handle_loop_logic()
        while mpr_ctrl[2].value: time.sleep(0.01)

    if mpr_ctrl[3].value:   # 볼륨 업
        adjust_volume(0.05)
        while mpr_ctrl[3].value: time.sleep(0.01)

    if mpr_ctrl[4].value:   # 볼륨 다운
        adjust_volume(-0.05)
        while mpr_ctrl[4].value: time.sleep(0.01)

    # Sensor1 연주 패드
    for i in range(12):
        if mpr_piano[i].value:
            play_note(i)
            while mpr_piano[i].value:
                time.sleep(0.012)

    time.sleep(0.008)
