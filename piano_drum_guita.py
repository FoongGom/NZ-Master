import time
import board
import busio
import adafruit_mpr121
from pygame import mixer, font, display, event
import os
import threading
import sys
import pygame

# ====================== [1. 하드웨어 초기화] ======================
i2c0 = busio.I2C(board.D1, board.D0)
i2c1 = busio.I2C(board.SCL, board.SDA)
mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)
mpr_ctrl = adafruit_mpr121.MPR121(i2c1, address=0x5A)

# ====================== [2. 오디오 설정 (너가 노이즈 잡은 그대로)] ======================
os.environ["SDL_AUDIODRIVER"] = "alsa"
if "AUDIODEV" in os.environ:
    del os.environ["AUDIODEV"]

mixer.pre_init(44100, -16, 2, 4096)
mixer.init()
mixer.set_num_channels(32)
print("🎵 PWM 오디오 출력 시작 (버퍼 확장 완료)")

# ====================== [3. 악기 데이터 (여기에 계속 추가 가능)] ======================
SOUND_BASE = "/home/noisezero/noise_zero/"

instruments = {
    "PIANO":  {"path": "piano/",  "color": (0, 255, 100)},
    "DRUM":   {"path": "drum/",   "color": (255, 80, 80)},
    "GUITAR": {"path": "guitar/", "color": (80, 180, 255)},
    # "BASS":   {"path": "bass/",   "color": (255, 200, 0)},
    # "SYNTH":  {"path": "synth/",  "color": (200, 100, 255)},
}

current_instrument = "PIANO"
sounds = {}                    # 현재 악기 사운드 캐시
full_notes = {}                # 악기별 노트 리스트

# 악기별 노트 리스트 (실제 파일명에 맞게 수정!)
full_notes["PIANO"] = ["AS1", "B1", "C2", "CS2", "D2", "DS2", "E2", "F2", "FS2", "G2", "GS2", "A2",
                       "AS2", "B2", "C3", "CS3", "D3", "DS3", "E3", "F3", "FS3", "G3", "GS3", "A3",
                       "AS3", "B3", "C4", "CS4", "D4", "DS4", "E4", "F4", "FS4", "G4", "GS4", "A4",
                       "AS4", "B4", "C5", "CS5", "D5", "DS5"]

full_notes["DRUM"] = ["KICK", "SNARE", "HAT", "CRASH", "TOM1", "TOM2", "RIDE", "CLAP", "PERC1", "PERC2", "COWBELL", "BELL"]
full_notes["GUITAR"] = ["E1", "F1", "FS1", "G1", "GS1", "A1", "AS1", "B1", "C2", "CS2", "D2", "DS2"]

def load_instrument(inst_name):
    global sounds
    sounds.clear()
    path = os.path.join(SOUND_BASE, instruments[inst_name]["path"])
    notes = full_notes.get(inst_name, [])
    
    for i, name in enumerate(notes):
        file_path = os.path.join(path, f"{name}.wav")
        if os.path.exists(file_path):
            sound = mixer.Sound(file_path)
            sound.set_volume(volume)
            sounds[i] = sound
        else:
            print(f"Missing: {file_path}")
    print(f"✅ {inst_name} 로드 완료 ({len(sounds)}개)")

# ====================== [4. 변수] ======================
base_index = 0
volume = 0.75
loop_state = "IDLE"
loop_data = []
is_looping = False
loop_start_time = 0
last_touch_time = 0

last_piano_states = [False] * 12
last_ctrl_states = [False] * 5

load_instrument(current_instrument)

# ====================== [5. GUI 초기화 (풀스크린)] ======================
pygame.init()
screen = display.set_mode((0, 0), pygame.FULLSCREEN)
display.set_caption("DIY Launchpad")
clock = pygame.time.Clock()

font_title = font.SysFont("malgun gothic", 130, bold=True)
font_sub = font.SysFont("malgun gothic", 55)
font_small = font.SysFont("malgun gothic", 40)

# ====================== [6. 함수] ======================
def switch_instrument():
    global current_instrument
    inst_list = list(instruments.keys())
    idx = inst_list.index(current_instrument)
    current_instrument = inst_list[(idx + 1) % len(inst_list)]
    load_instrument(current_instrument)

def adjust_volume(delta):
    global volume
    volume = max(0.1, min(1.0, volume + delta))
    for sound in sounds.values():
        if sound:
            sound.set_volume(volume)
    print(f"Volume: {int(volume*100)}%")

def play_note(pad_idx, record=True):
    global loop_data
    target_idx = base_index + pad_idx
    if target_idx in sounds and sounds[target_idx]:
        print(f"Playing: {current_instrument} {target_idx}")
        sounds[target_idx].play()
        if loop_state == "RECORDING" and record:
            loop_data.append((time.time() - loop_start_time, target_idx))

def draw_gui():
    w, h = screen.get_size()
    screen.fill((8, 8, 18))
    
    color = instruments[current_instrument]["color"]
    title = font_title.render(current_instrument, True, color)
    title_rect = title.get_rect(center=(w//2, h//3))
    screen.blit(title, title_rect)
    
    info = font_sub.render(f"옥타브 {base_index//12 + 1}     볼륨 {int(volume*100)}%", True, (220, 220, 220))
    screen.blit(info, info.get_rect(center=(w//2, h//2 + 100)))
    
    # 루프 상태
    if loop_state == "RECORDING":
        status = "● RECORDING"
        col = (255, 70, 70)
    elif loop_state == "PLAYING":
        status = "▶ LOOP PLAYING"
        col = (70, 255, 120)
    else:
        status = "IDLE"
        col = (140, 140, 140)
    status_text = font_small.render(status, True, col)
    screen.blit(status_text, (60, h - 100))
    
    display.flip()

# ====================== [7. 메인 루프] ======================
print("=== Launchpad with GUI 시작 ===")

try:
    while True:
        draw_gui()
        
        # ==================== 컨트롤 버튼 ====================
        for i in range(5):
            current_state = mpr_ctrl[i].value
            if current_state and not last_ctrl_states[i]:
                if i == 0:          # ← 악기 변환 버튼 (원하는 번호로 변경 가능)
                    switch_instrument()
                elif i == 1:        # Oct Up
                    base_index = min(30, base_index + 12)   # 최대치 조정 가능
                    print("Oct UP")
                elif i == 2:        # Oct Down
                    base_index = max(0, base_index - 12)
                    print("Oct DOWN")
                elif i == 3:        # 루프
                    handle_loop_logic()   # ← 이전 코드의 함수 그대로 사용
                elif i == 4:        # 볼륨 업 (필요시 down도 추가)
                    adjust_volume(0.05)
            last_ctrl_states[i] = current_state

        # ==================== 연주 패드 ====================
        for i in range(12):
            current_state = mpr_piano[i].value
            if current_state and not last_piano_states[i]:
                play_note(i)
            last_piano_states[i] = current_state

        # ESC 키로 종료
        for e in event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                raise KeyboardInterrupt

        clock.tick(30)
        time.sleep(0.008)

except KeyboardInterrupt:
    mixer.quit()
    pygame.quit()
    print("🚪 프로그램 종료")
