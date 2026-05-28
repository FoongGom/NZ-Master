# main_rpi.py
import socket
import numpy as np
import time
from anc_controller import ANC_Controller

ESP32_IP = "172.20.10.5"
UDP_PORT = 12345
BUFFER_SIZE = 256

controller = ANC_Controller(fs=16000, buffer_size=BUFFER_SIZE)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', UDP_PORT))
sock.settimeout(0.5)   # 타임아웃 늘림

print("=" * 60)
print("ANC 시스템 시작 - ESP32 연결 대기")
print("=" * 60)

while True:
    try:
        data, addr = sock.recvfrom(BUFFER_SIZE * 4)
        mic_samples = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        mic_samples = mic_samples >> 8

        result = controller.process(mic_samples)

        cmd = f"GAIN:{result['gain']:.3f},DELAY:{result['delay']}"
        sock.sendto(cmd.encode(), (ESP32_IP, UDP_PORT))

        print(f"[{result['method']}] Noise:{result['noise_type']} | Gain:{result['gain']:.2f} | Reduction:{result['estimated_db']:.1f}dB")

    except socket.timeout:
        print("ESP32 연결 대기중...")
        continue
    except Exception as e:
        print("Error:", e)
        time.sleep(0.1)
