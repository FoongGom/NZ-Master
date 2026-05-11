import pyaudio
import numpy as np

# [수정됨] INMP441은 드라이버 특성상 2채널(Stereo)로 열어야 에러가 나지 않습니다.
CHANNELS = 2          
RATE = 44100          
CHUNK = 1024          
FORMAT = pyaudio.paInt32  

p = pyaudio.PyAudio()

print("마이크 장치를 여는 중...")

try:
    # [수정됨] arecord -l에서 확인된 카드 번호 3번을 적용
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=3, 
                    frames_per_buffer=CHUNK)

    print("마이크 테스트 시작... 소리를 내보세요! (Ctrl+C로 종료)")

    while True:
        # 데이터 읽기
        data = stream.read(CHUNK, exception_on_overflow=False)
        
        # 바이트 데이터를 32비트 정수로 변환
        audio_data = np.frombuffer(data, dtype=np.int32)
        
        # [수정됨] 2채널 데이터 중 왼쪽 채널(저항 꽂은 쪽)만 슬라이싱해서 가져옴
        left_channel = audio_data[::2]
        
        # 소리 크기 계산 (절대값의 평균)
        amplitude = np.average(np.abs(left_channel))
        
        # 터미널에 시각화 (값에 따라 막대기 출력)
        # 소리가 너무 작게 찍히면 100000 숫자를 더 낮춰보세요 (예: 50000)
        bars = "█" * int(amplitude / 100000) 
        print(f"Volume: {amplitude:10.0f} {bars}")

except KeyboardInterrupt:
    print("\n사용자에 의해 중지됨")

except Exception as e:
    print(f"\n에러 발생: {e}")

finally:
    if 'stream' in locals():
        stream.stop_stream()
        stream.close()
    p.terminate()
