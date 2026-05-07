import sounddevice as sd
import numpy as np
import time
import csv
from smbus2 import SMBus
from scipy.signal import butter, lfilter

# =========================================
# 설정
# =========================================
SAMPLE_RATE = 16000
BLOCK_SIZE = 128
WINDOW_TIME = 0.1   # 0.1초 단위 처리

# MPU6050 I2C 주소
MPU_ADDR = 0x68

# =========================================
# I2C 초기화
# =========================================
bus = SMBus(1)

# MPU6050 활성화
bus.write_byte_data(MPU_ADDR, 0x6B, 0)

# =========================================
# 전역 버퍼
# =========================================
audio_buffer = []
vibration_buffer = []

# =========================================
# 진동 오프셋 보정
# =========================================
vib_offset = 0

def read_raw_vibration():
    high = bus.read_byte_data(MPU_ADDR, 0x3B)
    low = bus.read_byte_data(MPU_ADDR, 0x3C)

    value = (high << 8) | low

    if value > 32768:
        value -= 65536

    return value

def calibrate_vibration():
    global vib_offset

    print("진동 센서 보정 중... 건드리지 마세요")

    samples = []

    for _ in range(200):
        samples.append(read_raw_vibration())
        time.sleep(0.005)

    vib_offset = np.mean(samples)

    print("보정 완료")
    print("Offset:", vib_offset)

# =========================================
# 필터
# =========================================
def lowpass_filter(data, cutoff=200, fs=16000, order=4):
    nyquist = 0.5 * fs
    normal_cutoff = cutoff / nyquist

    b, a = butter(order, normal_cutoff, btype='low')

    return lfilter(b, a, data)

# =========================================
# DC 제거
# =========================================
def remove_dc(data):
    return data - np.mean(data)

# =========================================
# 정규화
# =========================================
def normalize(data):
    max_val = np.max(np.abs(data)) + 1e-6
    return data / max_val

# =========================================
# 스무딩
# =========================================
prev_audio_level = 0
prev_vib_level = 0

def smooth(prev, current, alpha=0.9):
    return alpha * prev + (1 - alpha) * current

# =========================================
# 특징 추출
# =========================================
def extract_features(audio, vibration):

    # ----- 오디오 안정화 -----
    audio = remove_dc(audio)
    audio = lowpass_filter(audio)
    audio = normalize(audio)

    # ----- 진동 안정화 -----
    vibration = vibration - vib_offset
    vibration = normalize(vibration)

    # ----- 특징 계산 -----
    audio_rms = np.sqrt(np.mean(audio**2))

    zcr = np.mean(
        np.abs(np.diff(np.sign(audio)))
    )

    vib_rms = np.sqrt(np.mean(vibration**2))

    vib_peak = np.max(np.abs(vibration))

    vib_var = np.var(vibration)

    return [
        audio_rms,
        zcr,
        vib_rms,
        vib_peak,
        vib_var
    ]

# =========================================
# CSV 저장
# =========================================
def save_to_csv(features):

    with open("dataset.csv", "a", newline="") as f:

        writer = csv.writer(f)

        writer.writerow(features)

# =========================================
# 오디오 콜백
# =========================================
def audio_callback(indata, frames, time_info, status):

    global audio_buffer

    if status:
        print(status)

    audio_buffer.extend(indata[:, 0])

# =========================================
# 메인 루프
# =========================================
def main():

    global audio_buffer
    global vibration_buffer
    global prev_audio_level
    global prev_vib_level

    calibrate_vibration()

    last_time = time.time()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype='float32',
        callback=audio_callback
    ):

        print("실시간 수집 시작")
        print("Ctrl+C 종료")

        while True:

            # 진동 읽기
            vibration = read_raw_vibration()

            vibration_buffer.append(vibration)

            # 일정 시간마다 처리
            current_time = time.time()

            if current_time - last_time >= WINDOW_TIME:

                if len(audio_buffer) > 0 and len(vibration_buffer) > 0:

                    # numpy 변환
                    audio_np = np.array(audio_buffer)
                    vib_np = np.array(vibration_buffer)

                    # 특징 추출
                    features = extract_features(
                        audio_np,
                        vib_np
                    )

                    # 스무딩
                    audio_level = smooth(
                        prev_audio_level,
                        features[0]
                    )

                    vib_level = smooth(
                        prev_vib_level,
                        features[2]
                    )

                    prev_audio_level = audio_level
                    prev_vib_level = vib_level

                    # 저장용 데이터
                    final_features = [
                        audio_level,
                        features[1],
                        vib_level,
                        features[3],
                        features[4]
                    ]

                    # CSV 저장
                    save_to_csv(final_features)

                    print("Saved:", final_features)

                # 버퍼 초기화
                audio_buffer = []
                vibration_buffer = []

                last_time = current_time

            # CPU 안정
            time.sleep(0.002)

# =========================================
# 실행
# =========================================
if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        print("\n종료됨")