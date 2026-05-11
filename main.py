import pyaudio
import numpy as np

# [범인 검거 완료] 마이크가 요구하는 정확한 스펙으로 수정합니다.
CHANNELS = 2          # 화면에 뜬 대로 2채널
RATE = 48000          # 화면에 뜬 대로 48000Hz (중요!)
CHUNK = 1024          
FORMAT = pyaudio.paInt32  # 화면에 뜬 대로 S32_LE (32비트)

p = pyaudio.PyAudio()

device_index = 3 # 확인된 카드 번호

print(f"마이크 연결 시도 중... (채널: {CHANNELS}, 샘플링레이트: {RATE})")

try:
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=CHUNK)

    print("!!! 드디어 연결 성공 !!! 박수를 쳐보세요. (종료: Ctrl+C)")

    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        audio_data = np.frombuffer(data, dtype=np.int32)
        
        # 2채널 중 우리가 저항 꽂은 왼쪽 채널 데이터만 가져오기
        left_channel = audio_data[::2]
            
        amplitude = np.average(np.abs(left_channel))
        
        # 터미널 시각화 (반응이 적으면 50000 숫자를 줄이세요)
        bars = "█" * int(amplitude / 50000) 
        print(f"Volume: {amplitude:10.0f} {bars}")

except Exception as e:
    print(f"\n[에러 발생]: {e}")

finally:
    if 'stream' in locals():
        stream.stop_stream()
        stream.close()
    p.terminate()
