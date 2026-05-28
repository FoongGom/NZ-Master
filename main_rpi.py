# main_rpi.py
import socket
import numpy as np
import time
from anc_controller import ANC_Controller

ESP32_IP = "172.20.10.5"
UDP_PORT = 12345

controller = ANC_Controller(fs=16000, buffer_size=256)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', UDP_PORT))
sock.settimeout(0.5)

print("=" * 60)
print("ANC 시스템 시작 - 데이터 수신 테스트")
print("=" * 60)

count = 0

while True:
    try:
        data, addr = sock.recvfrom(2048)   # 충분히 크게 받음
        
        count += 1
        print(f"[{count}] 수신됨 | 크기: {len(data)} bytes")

        # 데이터가 충분히 크면 처리
        if len(data) >= 256 * 4:
            mic_samples = np.frombuffer(data[:256*4], dtype=np.int32).copy()
            mic_samples = (mic_samples >> 8).astype(np.float32)

            result = controller.process(mic_samples)

            # ESP32로 명령 전송
            cmd = f"GAIN:{result['gain']:.3f},DELAY:{result['delay']}"
            sock.sendto(cmd.encode(), (ESP32_IP, UDP_PORT))

            print(f"  → 분석 완료: {result['method']} | Gain: {result['gain']:.2f} | Reduction: {result['estimated_db']:.1f}dB")
        else:
            print(f"  → 데이터 부족 (필요: 1024, 실제: {len(data)})")

    except socket.timeout:
        print("ESP32 연결 대기중...")
        continue
    except Exception as e:
        print(f"에러: {e}")
        time.sleep(0.1)
