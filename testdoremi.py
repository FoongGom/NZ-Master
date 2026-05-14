import time
import board
import busio
import adafruit_mpr121
import adafruit_ssd1306
from pygame import mixer
import os
import threading

# ====================== [1. 하드웨어 초기화] ======================
i2c0 = busio.I2C(board.D1, board.D0)      # Sensor1: 12개 연주 패드
i2c1 = busio.I2C(board.SCL, board.SDA)    # Sensor2: 컨트롤 + OLED

mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)
mpr_ctrl = adafruit_mpr121.MPR121(i2c1, address=0x5A)
oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c1)

# ====================== [2. 오디오 설정 - PWM용] ======================
mixer.pre_init(44100, -16, 2, 1024)   # 버퍼 키움
mixer.init()
os.environ["SDL_AUDIODRIVER"] = "alsa"

print("🎵 PWM 오디오 출력 시작 (GPIO 18 + 13)")

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
volume = 0.75          # 기본 볼륨 (0.0 ~ 1.0)
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

# 모든 사운드에 초기 볼륨 적용
for sound in piano_sounds:
    sound.set_volume(volume)

# ====================== [4. 함수] ======================
def update_ui(msg="Ready"):
    oled.fill(0)
    oled.text("Launchpad", 25, 8, 1)
    oled.text(f"Oct:{base_index//12}  Vol:{int(volume*100)}%", 8, 25, 1)
    oled.text(msg, 15, 42, 1)
    oled.show()

def adjust_volume(delta):
    global volume
    volume = max(0.1, min(1.0, volume + delta))
    for sound in piano_sounds:
        sound.set_volume(volume)
    update_ui("VOLUME ADJ")
    print(f"Volume: {int(volume*100)}%")

def play_note(pad_idx, record=True):
    global loop_data, loop_start_time
    target_idx = base_index + pad_idx
    if target_idx < len(piano_sounds):
        print(f"▶ Playing: {full_notes[target_idx]}")
        piano_sounds[target_idx].play()
        
        if loop_state == "RECORDING" and record:
            loop_data.append((time.time() - loop_start_time, target_idx))

def handle_loop_logic():
    global loop_state, last_touch_time, loop_data, loop_start_time, is_looping
    now = time.time()
    
    if now - last_touch_time < 0.35:   # 더블클릭 = 리셋
        is_looping = False
        loop_data = []
        loop_state = "IDLE"
        update_ui("LOOP RESET")
        return
    
    last_touch_time = now
    
    if loop_state == "IDLE":
        loop_state = "RECORDING"
        loop_data = []
        loop_start_time = time.time()
        update_ui("RECORDING...")
    elif loop_state == "RECORDING":
        loop_state = "PLAYING"
        is_looping = True
        threading.Thread(target=loop_player, daemon=True).start()
        update_ui("PLAYING LOOP")
    elif loop_state == "PLAYING":
        is_looping = False
        loop_state = "STOPPED"
        update_ui("LOOP STOPPED")

def loop_player():
    global is_looping
    while is_looping:
        start = time.time()
        for ts, idx in loop_data:
            while time.time() - start < ts:
                if not is_looping: return
                time.sleep(0.005)
            piano_sounds[idx].play()
        time.sleep(0.05)  # 루프 간격

# ====================== [5. 메인 루프] ======================
print("=== Launchpad PWM 버전 시작 ===")
update_ui("Ready")

while True:
    # ==================== Sensor2 컨트롤 버튼 ====================
    if mpr_ctrl[0].value:  # 0: Octave Up
        base_index = min(len(piano_sounds)-12, base_index + 12)
        update_ui(f"Oct UP")
        while mpr_ctrl[0].value: time.sleep(0.01)
        
    if mpr_ctrl[1].value:  # 1: Octave Down
        base_index = max(0, base_index - 12)
        update_ui(f"Oct DOWN")
        while mpr_ctrl[1].value: time.sleep(0.01)
        
    if mpr_ctrl[2].value:  # 2: Loop 버튼
        handle_loop_logic()
        while mpr_ctrl[2].value: time.sleep(0.01)
        
    if mpr_ctrl[3].value:  # 3: Volume Up
        adjust_volume(0.05)
        while mpr_ctrl[3].value: time.sleep(0.01)
        
    if mpr_ctrl[4].value:  # 4: Volume Down
        adjust_volume(-0.05)
        while mpr_ctrl[4].value: time.sleep(0.01)
    
    # ==================== Sensor1 연주 패드 ====================
    for i in range(12):
        if mpr_piano[i].value:
            play_note(i)
            while mpr_piano[i].value:
                time.sleep(0.012)
    
    time.sleep(0.008)
