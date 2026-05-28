# main_rpi.py
import socket
import time

ESP32_IP = "172.20.10.5"
UDP_PORT = 12345

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', UDP_PORT))
sock.settimeout(1.0)

print("=" * 60)
print("라즈베리파이 ANC 수신 테스트")
print("ESP32 연결 대기중...")
print("=" * 60)

count = 0

while True:
    try:
        data, addr = sock.recvfrom(2048)
        count += 1
        print(f"[{count}] ESP32로부터 데이터 수신! 크기: {len(data)} bytes")
        
        # 테스트로 gain 명령 보내기
        cmd = "GAIN:0.08,DELAY:10"
        sock.sendto(cmd.encode(), (ESP32_IP, UDP_PORT))
        
    except socket.timeout:
        print("ESP32 연결 대기중...")
    except Exception as e:
        print(f"에러: {e}")
    
    time.sleep(0.5)
