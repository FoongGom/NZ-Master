import pyaudio
import numpy as np

# 설정 (INMP441 사양 및 I2S 설정에 맞춤)
CHANNELS = 1          # L/R을 GND에 꽂으셨으므로 Mono(1) 설정
RATE = 44100          # 샘플링 레이트
CHUNK = 1024          # 한 번에 읽어올 데이터 양
FORMAT = pyaudio.paInt32  # INMP441은 24비트 데이터를 32비트로 전송함

p = pyaudio.PyAudio()

# 스트림 열기
stream = p.open(format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK)

print("마이크 테스트 시작... (Ctrl+C로 종료)")

try:
    while True:
        # 데이터 읽기
        data = stream.read(CHUNK, exception_on_overflow=False)
        # 바이트 데이터를 숫자로 변환
        audio_data = np.frombuffer(data, dtype=np.int32)
        
        # 소리 크기 계산 (절대값의 평균)
        amplitude = np.average(np.abs(audio_data))
        
        # 간단한 시각화 (막대 그래프 형태)
        bars = "█" * int(amplitude / 100000) # 감도에 따라 숫자 조절
        print(f"Volume: {amplitude:10.0f} {bars}")

except KeyboardInterrupt:
    print("\n중지됨")

finally:
    stream.stop_stream()
    stream.close()
    p.terminate()