# real_time/main_rpi.py
import socket
import numpy as np
import time
from anc_controller import ANC_Controller

ESP32_HOSTNAME = "anc-rpi.local"   # mDNS
UDP_PORT = 12345

controller = ANC_Controller(fs=16000, buffer_size=256)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', UDP_PORT))
sock.settimeout(0.1)

print("=== 층간소음 ANC 시스템 시작 (WiFi mDNS) ===")

while True:
    try:
        data, _ = sock.recvfrom(256 * 4)
        mic_data = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        mic_data = mic_data >> 8

        result = controller.process(mic_data)

        # ESP32로 명령 전송
        cmd = f"GAIN:{result['gain']:.3f},DELAY:{result['delay']}"
        sock.sendto(cmd.encode(), (ESP32_HOSTNAME, UDP_PORT))

        if int(time.time()) % 2 == 0:
            print(f"[{result['method']:7}] {result['noise_type']:12} | "
                  f"Gain:{result['gain']:.2f} | Reduction:{result['estimated_db']:.1f}dB")

    except socket.timeout:
        continue
    except Exception as e:
        print("Error:", e)
        time.sleep(0.01)
