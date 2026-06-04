# WebSocket 기반 상쇄간섭 업그레이드 버전

## 한 줄 설명

ESP32가 마이크로 소리를 듣고 Raspberry Pi로 보내면, Raspberry Pi가 가장 강한 주파수와 위상을 분석해서 같은 주파수의 반대 위상 소리를 만들어 ESP32로 돌려보내는 구조입니다.

## 기존 코드와 차이

기존 코드:

```text
마이크 입력
→ 저역통과필터
→ delay
→ -gain
→ 출력
```

업그레이드 코드:

```text
마이크 입력
→ FFT로 주요 주파수 찾기
→ 해당 주파수의 위상 추정
→ 같은 주파수의 사인파 생성
→ 위상을 180도 반전
→ WebSocket 지연 보정
→ ESP32로 상쇄 신호 전송
→ 스피커 출력
```

기존 방식도 반대 위상 개념은 있지만, 이번 버전은 "같은 주파수의 반대 위상 파형을 직접 만들어 출력"하기 때문에 상쇄간섭 설명에 더 적합합니다.

## 전체 구조

```text
INMP441 마이크
→ ESP32
→ WebSocket
→ Raspberry Pi
→ WebSocket
→ ESP32
→ GPIO25 DAC
→ PAM8403
→ 스피커
```

## 파일

```text
esp32_websocket_inmp441_anc.ino
rpi_websocket_destructive_interference_server.py
test_fake_esp32_interference_client.py
README.md
```

## 핵심 수식

원래 소리가 다음과 같다고 가정합니다.

```text
noise = A sin(2πft + phase)
```

상쇄 소리는 다음처럼 만듭니다.

```text
anti = gain × A × sin(2πft + phase + π + latency_correction)
```

여기서 `+π`가 180도 반대 위상입니다.

## Raspberry Pi 실행

```bash
pip install websockets numpy
python rpi_websocket_destructive_interference_server.py
```

예시:

```bash
python rpi_websocket_destructive_interference_server.py --gain 0.45 --latency-ms 140 --min-freq 30 --max-freq 400
```

## 하드웨어 없이 테스트

터미널 1:

```bash
python rpi_websocket_destructive_interference_server.py
```

터미널 2:

```bash
python test_fake_esp32_interference_client.py
```

정상이라면 서버에서 `freq=120.0Hz` 근처가 표시됩니다.

## 튜닝값

- `--gain`: 상쇄 소리 세기
- `--latency-ms`: WebSocket 왕복 지연 + 출력 지연 보정
- `--min-freq`, `--max-freq`: 분석할 주파수 범위

## 한계

상쇄간섭 설명에는 더 적합하지만 WebSocket 지연이 있으므로 순간 충격음에는 여전히 어렵습니다.

잘 맞는 대상:

```text
세탁기 웅웅거림
환풍기 소리
지속 저주파 소리
일정한 톤의 소리
```

어려운 대상:

```text
발소리
물건 낙하
짧은 충격음
불규칙한 소리
```
