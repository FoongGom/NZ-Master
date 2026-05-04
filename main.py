import time
import board
import busio
import adafruit_mpr121
import adafruit_ssd1306
from pygame import mixer
import os
import threading

# ====================== [1. 하드웨어 초기화] ======================
i2c0 = busio.I2C(board.D1, board.D0)      # Sensor1 - 12개 연주 패드
i2c1 = busio.I2C(board.SCL, board.SDA)    # Sensor2 - 4개 컨트롤 버튼

mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)  # 12개 연주용
mpr_ctrl  = adafruit_mpr121.MPR121(i2c1, address=0x5A)  # 4개 컨트롤용

oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c1)

# ====================== [2. 오디오 설정 - 테스트용] ======================
mixer.pre_init(44100, -16, 2, 512)
mixer.init()

# HDMI 모니터 스피커 또는 3.5mm 잭으로 강제 출력 (테스트 편의)
os.environ["SDL_AUDIODRIVER"] = "alsa"
# mixer.quit()
# mixer.init()   # 필요시 주석 해제

print("🎵 오디오 출력 테스트 중... HDMI/3.5mm 스피커로 나와야 합니다.")

SOUND_PATH = "/home/noisezero/noise_zero/piano/32음/"

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
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

# ====================== [4. 함수] ======================
def update_ui(msg="Ready"):
    oled.fill(0)
    oled.text("Launchpad", 25, 10, 1)
    oled.text(msg, 15, 35, 1)
    oled.show()

def play_note(pad_idx, record=True):
    global loop_data, loop_start_time
    target_idx = base_index + pad_idx
    
    if target_idx < len(piano_sounds):
        print(f"▶ Playing: {full_notes[target_idx]} (Pad {pad_idx})")  # 터미널에 출력
        piano_sounds[target_idx].play()
        
        if loop_state == "RECORDING" and record:
            loop_data.append((time.time() - loop_start_time, target_idx))

def handle_loop_logic():
    global loop_state, last_touch_time, loop_data, loop_start_time, is_looping
    
    now = time.time()
    if now - last_touch_time < 0.35:   # 더블클릭 리셋
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
        time.sleep(0.1)

# ====================== [5. 메인 루프] ======================
print("=== Launchpad 테스트 시작 ===")
update_ui("Ready")

while True:
    # 컨트롤 버튼 (Sensor2)
    if mpr_ctrl[0].value:   # Octave Up
        base_index = min(len(piano_sounds)-12, base_index + 12)
        update_ui(f"Oct UP {base_index}")
        while mpr_ctrl[0].value: time.sleep(0.01)
        
    if mpr_ctrl[1].value:   # Octave Down
        base_index = max(0, base_index - 12)
        update_ui(f"Oct DOWN {base_index}")
        while mpr_ctrl[1].value: time.sleep(0.01)
        
    if mpr_ctrl[2].value:   # Loop
        handle_loop_logic()
        while mpr_ctrl[2].value: time.sleep(0.01)

    # 연주 패드 12개 (Sensor1)
    for i in range(12):
        if mpr_piano[i].value:
            play_note(i)
            while mpr_piano[i].value: 
                time.sleep(0.012)
    
    time.sleep(0.008)
