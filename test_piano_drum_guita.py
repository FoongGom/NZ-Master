import time
import os
import pygame
import board
import busio
import adafruit_mpr121

from pygame import mixer
from pygame import display
from pygame import event

# =========================================================
# [1] HARDWARE INIT
# =========================================================

i2c0 = busio.I2C(board.D1, board.D0)
i2c1 = busio.I2C(board.SCL, board.SDA)

mpr_piano = adafruit_mpr121.MPR121(i2c0, address=0x5A)
mpr_ctrl = adafruit_mpr121.MPR121(i2c1, address=0x5A)

# =========================================================
# [2] AUDIO INIT
# =========================================================

os.environ["SDL_AUDIODRIVER"] = "alsa"

if "AUDIODEV" in os.environ:
    del os.environ["AUDIODEV"]

mixer.pre_init(
    44100,
    -16,
    2,
    1024
)

pygame.init()

mixer.init()

# Raspberry Pi 안정성 위해 줄임
mixer.set_num_channels(24)

# =========================================================
# [3] INSTRUMENT DATA
# =========================================================

SOUND_BASE = "/home/noisezero/noise_zero/"

instruments = {

    "PIANO": {
        "path": "piano/",
        "color": (0, 255, 120)
    },

    "DRUM": {
        "path": "drum/",
        "color": (255, 80, 80)
    },

    "GUITAR": {
        "path": "guitar/",
        "color": (80, 180, 255)
    }
}

full_notes = {

    "PIANO": [

        "AS1", "B1", "C2", "CS2",
        "D2", "DS2", "E2", "F2",
        "FS2", "G2", "GS2", "A2",

        "AS2", "B2", "C3", "CS3",
        "D3", "DS3", "E3", "F3",
        "FS3", "G3", "GS3", "A3",

        "AS3", "B3", "C4", "CS4",
        "D4", "DS4", "E4", "F4",
        "FS4", "G4", "GS4", "A4",

        "AS4", "B4", "C5", "CS5",
        "D5", "DS5"
    ],

    "DRUM": [

        "HihatClosed",
        "HiHatOpen",
        "Kick",
        "Snare",

        "TomHi",
        "TomMid",
        "TomLow",
        "Rim",

        "Ride",
        "Clap",
        "Crash1",
        "Crash2"
    ],

    "GUITAR": [

        "GuitarE1", "GuitarF1",
        "GuitarFS1", "GuitarG1",
        "GuitarGS1", "GuitarA1",
        "GuitarAS1", "GuitarB1",

        "GuitarC2", "GuitarCS2",
        "GuitarD2", "GuitarDS2",

        "GuitarE2", "GuitarF2",
        "GuitarFS2", "GuitarG2",

        "GuitarGS2", "GuitarA2",
        "GuitarAS2", "GuitarB2",

        "GuitarC3", "GuitarCS3",
        "GuitarD3", "GuitarDS3",

        "GuitarE3", "GuitarF3",
        "GuitarFS3", "GuitarG3",

        "GuitarGS3", "GuitarA3",
        "GuitarAS3", "GuitarB3",

        "GuitarC4", "GuitarCS4",
        "GuitarD4", "GuitarDS4",

        "GuitarE4", "GuitarF4",
        "GuitarFS4", "GuitarG4",

        "GuitarGS4", "GuitarA4",
        "GuitarAS4", "GuitarB4",

        "GuitarC5", "GuitarCS5",
        "GuitarD5", "GuitarDS5",

        "GuitarC1", "GuitarCS1",
        "GuitarD1", "GuitarDS1"
    ]
}

# =========================================================
# [4] GLOBAL STATE
# =========================================================

current_instrument = "PIANO"

sounds = {}
all_sounds = {}

base_index = 0

volume = 0.30

is_recording = False
is_looping = False

loop_data = []

loop_length = 0

loop_start_time = 0
loop_cycle_start = 0

current_note = ""
note_display_time = 0

loop_press_time = 0
loop_held = False

last_ctrl_states = [False] * 6
last_piano_states = [False] * 12

# HOLD REPEAT

held_notes = {}

repeat_interval = 0.18

# =========================================================
# [5] LOAD INSTRUMENT
# =========================================================

def load_instrument(inst_name):

    global sounds
    global base_index

    sounds.clear()

    path = os.path.join(
        SOUND_BASE,
        instruments[inst_name]["path"]
    )

    notes = full_notes.get(inst_name, [])

    loaded = 0

    for i, name in enumerate(notes):

        file_path = os.path.join(
            path,
            f"{name}.wav"
        )

        if os.path.exists(file_path):

            sound = mixer.Sound(file_path)

            sound.set_volume(volume)

            sounds[i] = sound

            all_sounds[(inst_name, i)] = sound

            loaded += 1

        else:

            print(f"⚠ Missing: {file_path}")

    base_index = 0

    print(f"✅ {inst_name} 로드 완료 ({loaded}개)")

load_instrument(current_instrument)

# =========================================================
# [6] GUI
# =========================================================

screen = display.set_mode(
    (0, 0),
    pygame.FULLSCREEN
)

clock = pygame.time.Clock()

try:

    font_big = pygame.font.Font(
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        85
    )

    font_sub = pygame.font.Font(
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        48
    )

    font_small = pygame.font.Font(
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        36
    )

except:

    font_big = pygame.font.SysFont(
        "nanumgothic",
        85,
        bold=True
    )

    font_sub = pygame.font.SysFont(
        "nanumgothic",
        48
    )

    font_small = pygame.font.SysFont(
        "nanumgothic",
        36
    )

# =========================================================
# [7] FUNCTIONS
# =========================================================

def switch_instrument():

    global current_instrument

    inst_list = list(instruments.keys())

    idx = inst_list.index(current_instrument)

    current_instrument = inst_list[
        (idx + 1) % len(inst_list)
    ]

    load_instrument(current_instrument)

def adjust_volume(delta):

    global volume

    volume = max(
        0.1,
        min(1.0, volume + delta)
    )

    for s in sounds.values():

        if s:
            s.set_volume(volume)

    for s in all_sounds.values():

        if s:
            s.set_volume(volume)

def play_note(pad_idx, record=True):

    global current_note
    global note_display_time

    target_idx = base_index + pad_idx

    if target_idx not in sounds:
        return

    snd = sounds[target_idx]

    if snd is None:
        return

    note_name = full_notes.get(
        current_instrument,
        []
    )[target_idx]

    current_note = note_name

    note_display_time = time.time()

    # 안정성 위해 True 제거
    ch = mixer.find_channel()

    if ch:

        ch.set_volume(volume)

        ch.play(snd)

    # ================= RECORD =================

    if is_recording and record:

        rel_time = (
            time.time() - loop_start_time
        )

        if loop_length > 0:

            rel_time = (
                rel_time % loop_length
            )

        loop_data.append({

            "ts": rel_time,

            "inst": current_instrument,

            "idx": target_idx,

            "played": False
        })

def draw_gui():

    global current_note
    global note_display_time

    w, h = screen.get_size()

    screen.fill((5, 5, 18))

    # ================= INSTRUMENT =================

    inst_color = instruments[
        current_instrument
    ]["color"]

    inst = font_sub.render(
        current_instrument,
        True,
        inst_color
    )

    screen.blit(inst, (50, 40))

    # ================= NOTE =================

    if time.time() - note_display_time < 1.0:

        note = font_big.render(
            current_note,
            True,
            (255, 240, 180)
        )

        screen.blit(
            note,
            note.get_rect(
                center=(w // 2, h // 2 - 20)
            )
        )

    # ================= INFO =================

    octave = "-"

    if current_instrument != "DRUM":

        octave = str(
            base_index // 12 + 1
        )

    info = font_sub.render(

        f"OCT {octave}   VOL {int(volume*100)}%",

        True,

        (200, 200, 230)
    )

    screen.blit(
        info,
        info.get_rect(
            center=(w // 2, h // 2 + 100)
        )
    )

    # ================= STATUS =================

    if is_recording and loop_length == 0:

        status = "● RECORDING BASE"
        col = (255, 80, 80)

    elif is_recording:

        status = "● OVERDUB"
        col = (255, 180, 80)

    elif is_looping:

        status = "▶ LOOP PLAY"

        col = (80, 255, 140)

    else:

        status = "READY"

        col = (120, 120, 150)

    st = font_small.render(
        status,
        True,
        col
    )

    screen.blit(
        st,
        (
            w - st.get_width() - 50,
            h - 80
        )
    )

    display.flip()

# =========================================================
# [8] MAIN LOOP
# =========================================================

print("=== Launchpad Looper Stable v18 ===")

try:

    while True:

        now = time.time()

        draw_gui()

        # =====================================================
        # LOOP PLAYBACK
        # =====================================================

        if is_looping and loop_length > 0:

            now_loop = (
                time.time() - loop_cycle_start
            )

            # 루프 리셋
            if now_loop >= loop_length:

                loop_cycle_start = time.time()

                now_loop = 0

                for note in loop_data:

                    note["played"] = False

            # 노트 재생
            for note in loop_data:

                if (
                    now_loop >= note["ts"]
                    and not note["played"]
                ):

                    key = (
                        note["inst"],
                        note["idx"]
                    )

                    if key in all_sounds:

                        ch = mixer.find_channel()

                        if ch:

                            ch.set_volume(volume)

                            ch.play(all_sounds[key])

                    note["played"] = True

        # =====================================================
        # LOOP BUTTON
        # =====================================================

        loop_btn = mpr_ctrl[3].value

        if loop_btn and not loop_held:

            loop_press_time = time.time()

            loop_held = True

        elif not loop_btn and loop_held:

            held_time = (
                time.time() - loop_press_time
            )

            # ================= RESET =================

            if held_time > 3.0:

                is_looping = False

                is_recording = False

                loop_data.clear()

                loop_length = 0

                print("🔄 FULL RESET")

            else:

                # ================= START RECORD =================

                if not is_recording and not is_looping:

                    is_recording = True

                    loop_data.clear()

                    loop_start_time = time.time()

                    print("⏺ BASE RECORD START")

                # ================= STOP BASE RECORD =================

                elif is_recording and loop_length == 0:

                    is_recording = False

                    loop_length = (
                        time.time() - loop_start_time
                    )

                    if loop_length > 0:

                        is_looping = True

                        loop_cycle_start = time.time()

                    print(
                        f"▶ LOOP START "
                        f"{loop_length:.2f}s"
                    )

                # ================= OVERDUB TOGGLE =================

                else:

                    if not is_recording:

                        is_recording = True

                        loop_start_time = (
                            time.time()
                            - (
                                (
                                    time.time()
                                    - loop_cycle_start
                                )
                                % loop_length
                            )
                        )

                        print("➕ OVERDUB START")

                    else:

                        is_recording = False

                        print("⏸ OVERDUB STOP")

            loop_held = False

        # =====================================================
        # CONTROL BUTTONS
        # =====================================================

        for i in range(6):

            if i == 3:
                continue

            state = mpr_ctrl[i].value

            if state and not last_ctrl_states[i]:

                # 악기 변경
                if i == 0:

                    switch_instrument()

                # 옥타브 +
                elif (
                    i == 1
                    and current_instrument != "DRUM"
                ):

                    base_index = min(
                        30,
                        base_index + 12
                    )

                # 옥타브 -
                elif (
                    i == 2
                    and current_instrument != "DRUM"
                ):

                    base_index = max(
                        0,
                        base_index - 12
                    )

                # 볼륨 +
                elif i == 4:

                    adjust_volume(0.05)

                # 볼륨 -
                elif i == 5:

                    adjust_volume(-0.05)

            last_ctrl_states[i] = state

        # =====================================================
        # PIANO PADS
        # =====================================================

        for i in range(12):

            state = mpr_piano[i].value

            # ================= PRESS =================

            if state:

                # 최초 터치
                if not last_piano_states[i]:

                    play_note(i)

                    held_notes[i] = now

                # HOLD REPEAT
                elif (
                    i in held_notes
                    and now - held_notes[i]
                    >= repeat_interval
                ):

                    play_note(i)

                    held_notes[i] = now

            # ================= RELEASE =================

            else:

                if i in held_notes:

                    del held_notes[i]

            last_piano_states[i] = state

        # =====================================================
        # EXIT
        # =====================================================

        for e in event.get():

            if (
                e.type == pygame.QUIT
                or (
                    e.type == pygame.KEYDOWN
                    and e.key == pygame.K_ESCAPE
                )
            ):

                raise KeyboardInterrupt

        clock.tick(120)

        time.sleep(0.001)

except KeyboardInterrupt:

    mixer.quit()

    pygame.quit()

    print("프로그램 종료")
