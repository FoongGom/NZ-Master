import pyaudio
import numpy as np

p = pyaudio.PyAudio()

# 1. 'googlevoicehat'이라는 이름이 들어간 장치의 인덱스 찾기
target_name = "googlevoicehat"
device_index = -1

print("--- 오디오 장치 스캔 중 ---")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    print(f"Index {i}: {info['name']}")
    if target_name in info['name'].lower():
        device_index = i

if device_index == -1:
    print("\n[에러] 마이크 장치를 찾을 수 없습니다. arecord -l을 다시 확인하세요.")
    p.terminate()
    exit()

# 2. 해당 장치의 실제 최대 채널 수 가져오기
device_info = p.get_device_info_by_index(device_index)
CHANNELS = int(device_info['maxInputChannels'])
RATE = 48000 # 아까 확인한 하드웨어 매개변수 값
CHUNK = 1024
FORMAT = pyaudio.paInt32

print(f"\n최종 타겟 장치 인덱스: {device_index}")
print(f"인식된 최대 채널 수: {CHANNELS}")

try:
    # 3. 채널이 0으로 인식된다면 강제로 2로 설정 (드라이버 버그 대비)
    stream = p.open(format=FORMAT,
                    channels=CHANNELS if CHANNELS > 0 else 2,
                    rate=RATE,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=CHUNK)

    print("\n!!! 드디어 연결되었습니다 !!!")
    print("마이크에 소리를 내보세요. (종료: Ctrl+C)")

    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        audio_data = np.frombuffer(data, dtype=np.int32)
        
        # 2채널일 경우 왼쪽 소리만 추출
        if (CHANNELS if CHANNELS > 0 else 2) == 2:
            audio_data = audio_data[::2]
            
        amplitude = np.average(np.abs(audio_data))
        bars = "█" * int(amplitude / 50000)
        print(f"Volume: {amplitude:10.0f} {bars}")

except Exception as e:
    print(f"\n[최종 실패]: {e}")
    print("이 단계에서도 안 된다면, i2s-mmap 관련 커널 설정이 꼬였을 가능성이 큽니다.")

finally:
    if 'stream' in locals():
        stream.stop_stream()
        stream.close()
    p.terminate()
