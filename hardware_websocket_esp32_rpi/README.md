
# WebSocket 기반 ESP32 + Raspberry Pi 공기 중 층간소음 저감 프로토타입

## 전체 구조

```text
INMP441 마이크
→ ESP32
→ WebSocket
→ Raspberry Pi
→ WebSocket
→ ESP32
→ DAC GPIO25
→ PAM8403 앰프
→ 스피커
```

## 목표

완전 상쇄가 아니라, 공기 중으로 전달된 층간소음의 저주파 성분을 감지하고 Raspberry Pi에서 반대 위상 신호를 계산한 뒤 ESP32가 출력하여 일부 저감 가능성을 확인하는 프로토타입입니다.

## 파일

- `esp32_websocket_inmp441_anc.ino`
  - ESP32 Arduino 코드
  - INMP441 마이크 입력
  - WebSocket 클라이언트
  - Raspberry Pi로 마이크 프레임 전송
  - Raspberry Pi에서 받은 상쇄 프레임을 DAC GPIO25로 출력

- `rpi_websocket_anc_server.py`
  - Raspberry Pi Python 코드
  - WebSocket 서버
  - ESP32에서 받은 마이크 프레임 처리
  - 저역통과필터, delay, gain, 반대 위상 처리
  - ESP32로 상쇄 프레임 반환

## ESP32 연결

```text
INMP441 VDD  → ESP32 3.3V
INMP441 GND  → ESP32 GND
INMP441 SCK  → ESP32 GPIO14
INMP441 WS   → ESP32 GPIO15
INMP441 SD   → ESP32 GPIO32
INMP441 L/R  → GND
```

## ESP32 출력 연결

일반 ESP32 DevKit 기준:

```text
ESP32 GPIO25(DAC1)
→ 1uF~10uF 커플링 캐패시터 권장
→ PAM8403 L-IN 또는 R-IN

ESP32 GND
→ PAM8403 GND

PAM8403 OUT
→ 스피커
```

주의: ESP32-S3, ESP32-C3는 내장 DAC가 없을 수 있습니다. 이 코드는 일반 ESP32 DevKit의 GPIO25 DAC 출력을 기준으로 합니다.

## Raspberry Pi 설치

```bash
pip install websockets numpy
```

## Raspberry Pi 서버 실행

```bash
python rpi_websocket_anc_server.py
```

옵션 예시:

```bash
python rpi_websocket_anc_server.py --gain 0.35 --delay-ms 80 --cutoff 200
```

## ESP32 코드 수정

`esp32_websocket_inmp441_anc.ino`에서 아래를 본인 환경에 맞게 수정하세요.

```cpp
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* RPI_HOST = "192.168.0.25";
```

`RPI_HOST`는 Raspberry Pi의 IP 주소입니다.

## Arduino IDE 준비

Arduino IDE에서 설치:

- ESP32 보드 패키지
- WebSockets by Markus Sattler

보드 선택:

```text
ESP32 Dev Module
```

## 동작 흐름

```text
1. ESP32가 INMP441 마이크로 소리 샘플을 읽음
2. 1000Hz로 다운샘플링한 프레임을 WebSocket으로 Raspberry Pi에 보냄
3. Raspberry Pi가 DC 제거, 저역통과필터, delay, -gain 처리를 함
4. Raspberry Pi가 상쇄 프레임을 ESP32에 다시 보냄
5. ESP32가 GPIO25 DAC로 출력
6. PAM8403이 증폭
7. 스피커가 상쇄 소리 출력
```

## 튜닝값

Raspberry Pi 코드 실행 옵션에서 조절:

- `--gain`
  - 출력 세기
  - 처음에는 0.2 ~ 0.4 권장

- `--delay-ms`
  - 지연 보정
  - WebSocket 왕복 지연이 있으므로 60~150ms 사이 테스트

- `--cutoff`
  - 저역통과필터 기준 주파수
  - 세탁기 웅웅거림: 120~250Hz
  - 사람 말소리 낮은 성분: 300~600Hz

## 한계

이 구조는 WebSocket 왕복 통신을 사용하므로 지연이 큽니다.  
따라서 발소리나 물건 낙하처럼 순간적인 충격음 상쇄에는 적합하지 않습니다.

대신 아래와 같은 지속적인 소음 실험에 더 적합합니다.

```text
세탁기 웅웅거림
환풍기 소리
사람 말소리의 낮은 성분
지속적인 저주파 소리
```
