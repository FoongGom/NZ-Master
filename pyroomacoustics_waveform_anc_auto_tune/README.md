# Pyroomacoustics Waveform ANC 자동 튜닝 버전

## 목적

기존 결과에서 `Reduction = -2.00 dB`가 나왔습니다.  
이건 상쇄 신호가 소리를 줄이지 못하고 오히려 키웠다는 뜻입니다.

그래서 이 버전은 `gain`, `latency_ms`, `polarity`를 자동으로 바꿔가면서  
가장 잘 줄어드는 조합을 찾습니다.

## 실행

```bash
python pyroomacoustics_waveform_anc_auto_tune.py
```

## 결과 해석

```text
reduction 양수 → 상쇄 성공
reduction 0 근처 → 변화 거의 없음
reduction 음수 → 오히려 커짐
```

## 실제 코드에 반영할 값

실행 결과에서 아래 값을 확인합니다.

```text
Best gain
Best latency
Best polarity
```

예:

```text
Best gain      : 0.15
Best latency   : 40 ms
Best polarity  : -1
```

그러면 실제 Raspberry Pi 서버 실행 옵션에 반영합니다.

```bash
python rpi_websocket_waveform_anc_server.py --gain 0.15 --latency-ms 40
```

`Best polarity`가 `+1`이면 실제 코드의 핵심 줄도 비교해야 합니다.

기존:

```python
anti = -self.gain * delayed
```

비교용:

```python
anti = self.gain * delayed
```
