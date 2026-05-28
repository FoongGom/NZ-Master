# main_rpi.py
import socket
import numpy as np
import time
from anc_controller import ANC_Controller

# ================== 설정 ==================
ESP32_IP = "172.20.10.5"      # ← 라즈베리파이 현재 IP (고정)
UDP_PORT = 12345
BUFFER_SIZE = 256

# ANC Controller 초기화
controller = ANC_Controller(fs=16000, buffer_size=BUFFER_SIZE)

# UDP 소켓 설정
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', UDP_PORT))
sock.settimeout(0.1)

print("=" * 70)
print("층간소음 ANC 시스템 시작 (WiFi)")
print(f"Raspberry Pi IP: {ESP32_IP}")
print("ESP32 연결 대기 중...")
print("=" * 70)

last_print = time.time()

while True:
    try:
        # ESP32로부터 마이크 데이터 수신
        data, addr = sock.recvfrom(BUFFER_SIZE * 4)
        
        mic_samples = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        mic_samples = mic_samples >> 8   # 24bit 정렬

        # ANC 분석 및 최적 제어 방식 결정
        result = controller.process(mic_samples)

        # ESP32로 제어 명령 전송
        cmd = f"GAIN:{result['gain']:.3f},DELAY:{result['delay']}"
        sock.sendto(cmd.encode(), (ESP32_IP, UDP_PORT))

        # 2초마다 상태 출력
        if time.time() - last_print > 2.0:
            last_print = time.time()
            print(f"[{result['method']:7}] Noise: {result['noise_type']:12} | "
                  f"Gain: {result['gain']:.2f} | Est.Reduction: {result['estimated_db']:.1f}dB")

    except socket.timeout:
        continue
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(0.01)
