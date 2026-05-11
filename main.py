import pyaudio
import numpy as np

# [수정] 다시 1채널로 시도합니다.
CHANNELS = 1          
RATE = 44100          
CHUNK = 1024          
FORMAT = pyaudio.paInt32  

p = pyaudio.PyAudio()

print("마이크 장치를 여는 중 (Card 3, Channel 1)...")

try:
    # [체크] input_device_index가 3이 맞는지 다시 확인 (arecord -l 기준)
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=3, 
                    frames_per_buffer=CHUNK)

    print("마이크 테스트 시작... (Ctrl+C로 종료)")

    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        audio_data = np.frombuffer(data, dtype=np.int32)
        
        # 소리 크기 계산
        amplitude = np.average(np.abs(audio_data))
        
        # 감도가 너무 낮으면 100000을 10000 정도로 낮춰보세요.
        bars = "█" * int(amplitude / 10000) 
        print(f"Volume: {amplitude:10.0f} {bars}")

except KeyboardInterrupt:
    print("\n중지됨")
except Exception as e:
    # 여기서 또 에러나면 장치 정보를 상세히 출력하도록 함
    print(f"\n에러 발생: {e}")
    info = p.get_device_info_by_index(3)
    print(f"장치 정보: 최대 입력 채널 수 = {info['maxInputChannels']}")

finally:
    if 'stream' in locals():
        stream.stop_stream()
        stream.close()
    p.terminate()
