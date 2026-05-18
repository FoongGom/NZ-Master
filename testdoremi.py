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

# ====================== [2. 오디오 설정 최적화] ======================
# [변경] 외부 USB 카드용 설정을 지우고, 라즈베리 파이 내장 ALSA(PWM) 드라이버를 강제 지정합니다.
os.environ["SDL_AUDIODRIVER"] = "alsa"
if "AUDIODEV" in os.environ:
    del os.environ["AUDIODEV"]

# [변경] 깨짐 방지를 위해 버퍼 크기를 1024에서 4096으로 대폭 늘렸습니다.
# 만약 소리가 밀리는 느낌이 든다면 2048로 낮춰보세요.
mixer.pre_init(44100, -16, 2, 4096)
mixer.init()

# 다중 채널을 넉넉히 열어 소리가 겹칠 때 끊어지는 현상을 방지합니다.
mixer.set_num_channels(32)

print("🎵 PWM 오디오 출력 시작 (버퍼 확장 완료)")

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

# ====================== [3. 변수 및 상태 관리] ======================
base_index = 2
volume = 0.75
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

# [추가] 패드를 누르고 있는 상태를 기억하여 노이즈와 프레임 드랍을 막습니다.
last_piano_states = [False] * 12
last_ctrl_states = [False] * 5

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
    # 1. Sensor2 컨트롤 처리 (Edge Trigger 적용)
    for i in range(5):
        current_state = mpr_ctrl[i].value
        if current_state and not last_ctrl_states[i]:  # 누르는 순간 '한 번만' 실행
            if i == 0:    # 옥타브 업
                base_index = min(len(piano_sounds)-12, base_index + 12)
                print("Oct UP")
            elif i == 1:  # 옥타브 다운
                base_index = max(0, base_index - 12)
                print("Oct DOWN")
            elif i == 2:  # 루프
                handle_loop_logic()
            elif i == 3:  # 볼륨 업
                adjust_volume(0.05)
            elif i == 4:  # 볼륨 다운
                adjust_volume(-0.05)
        last_ctrl_states[i] = current_state

    # 2. Sensor1 연주 패드 처리
    # [개선] 기존의 'while mpr_piano[i].value: time.sleep(0.012)' 구문을 제거했습니다.
    # 패드를 누르고 있는 동안 프로그램 전체가 멈춰서 오디오 버퍼를 채우지 못해 소리가 깨지던 핵심 원인입니다.
    for i in range(12):
        current_state = mpr_piano[i].value
        if current_state and not last_piano_states[i]:  # 새로 꾹 누르는 그 타이밍에만 재생
            play_note(i)
        last_piano_states[i] = current_state

    time.sleep(0.01)  # CPU 사용량 스파이크 방지용 미세 딜레이
