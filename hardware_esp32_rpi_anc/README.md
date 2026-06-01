# ESP32 + Raspberry Pi + PAM8403 공기 중 층간소음 저감 프로토타입

## 구조

```text
INMP441 마이크
→ ESP32
→ USB Serial
→ Raspberry Pi
→ PAM8403 앰프
→ 스피커
```

## 목표

완전 상쇄가 아니라, 공기 중으로 전달된 층간소음의 저주파 성분을 감지하고 반대 위상 제어 신호를 출력해 일부 저감 가능성을 확인하는 프로토타입입니다.

## 파일

- `esp32_inmp441_serial_stream.ino`
  - ESP32 코드
  - INMP441 마이크를 읽고 Raspberry Pi로 샘플 전송

- `rpi_serial_esp32_airborne_anc.py`
  - Raspberry Pi 코드
  - ESP32에서 받은 샘플을 처리해서 반대 위상 신호를 스피커로 출력

## ESP32 연결

```text
INMP441 VDD  → ESP32 3.3V
INMP441 GND  → ESP32 GND
INMP441 SCK  → ESP32 GPIO14
INMP441 WS   → ESP32 GPIO15
INMP441 SD   → ESP32 GPIO32
INMP441 L/R  → GND
```

## Raspberry Pi 출력 연결

Raspberry Pi의 오디오 출력이 PAM8403으로 들어가야 합니다.

예시:

```text
Raspberry Pi 오디오 출력 L/R
→ PAM8403 L-IN/R-IN
→ 스피커
```

PAM8403은 아날로그 앰프이므로, Raspberry Pi에서 아날로그 오디오 출력이 나와야 합니다.
필요 시 USB 사운드카드나 DAC가 필요할 수 있습니다.

## Raspberry Pi 설치

```bash
pip install numpy scipy sounddevice pyserial
```

## ESP32 업로드

Arduino IDE에서 `esp32_inmp441_serial_stream.ino` 업로드.

## Raspberry Pi 실행

오디오 장치 확인:

```bash
python rpi_serial_esp32_airborne_anc.py --list-audio-devices
```

기본 실행:

```bash
python rpi_serial_esp32_airborne_anc.py --serial-port /dev/ttyUSB0
```

출력 장치 지정:

```bash
python rpi_serial_esp32_airborne_anc.py --serial-port /dev/ttyUSB0 --output-device 0
```

조절 예시:

```bash
python rpi_serial_esp32_airborne_anc.py --serial-port /dev/ttyUSB0 --gain 0.25 --delay-ms 60 --cutoff 180
```

## 튜닝값

- `--gain`
  - 반대 위상 출력 세기
  - 처음에는 0.15 ~ 0.30 권장

- `--delay-ms`
  - 반대 신호 지연 시간
  - 40 ~ 100ms 사이에서 테스트

- `--cutoff`
  - 저역통과필터 기준 주파수
  - 세탁기 웅웅거림: 120 ~ 250Hz 권장
  - 사람 말소리 낮은 성분: 300 ~ 600Hz 가능

## 한계

ESP32에서 Raspberry Pi로 데이터를 보내는 구조라 지연이 있습니다.
따라서 발소리나 물건 낙하처럼 순간적인 충격음은 어렵고,
세탁기 웅웅거림이나 사람 말소리 낮은 성분처럼 지속되는 소리에 더 적합합니다.
