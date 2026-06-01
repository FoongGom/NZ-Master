
"""
파일명: rpi_websocket_anc_server.py

=========================================================
이 코드가 하는 일
=========================================================

이 코드는 Raspberry Pi에서 실행하는 Python 코드입니다.

쉽게 말하면 라즈베리파이가 하는 일은 3가지입니다.

1. ESP32가 보낸 마이크 소리 데이터를 받는다.
2. 받은 소리 데이터를 분석해서 "반대 소리"를 만든다.
3. 만든 반대 소리를 다시 ESP32로 보낸다.

전체 구조는 아래와 같습니다.

[INMP441 마이크]
    ↓
[ESP32]
    ↓ WebSocket
[Raspberry Pi: 이 Python 코드]
    ↓ WebSocket
[ESP32]
    ↓
[PAM8403 앰프]
    ↓
[스피커]

=========================================================
왜 Raspberry Pi가 필요한가?
=========================================================

ESP32는 마이크 입력과 스피커 출력에 적합하고,
Raspberry Pi는 Python으로 소리 분석과 DSP 계산을 하기 쉽습니다.

그래서 Raspberry Pi는 "계산 담당"입니다.

=========================================================
핵심 처리 흐름
=========================================================

ESP32에서 받은 소리
→ DC offset 제거
→ 저역통과필터
→ delay
→ -gain
→ 출력 제한
→ ESP32로 다시 전송

핵심 수식은 아래 한 줄입니다.

anti = -gain * delayed

뜻:
- delayed: 타이밍을 맞추기 위해 조금 늦춘 소리
- gain: 스피커로 낼 소리의 세기
- - 부호: 반대 위상으로 뒤집는 것
- anti: 스피커로 출력할 상쇄 소리

=========================================================
설치
=========================================================

pip install websockets numpy

실행:

python rpi_websocket_anc_server.py

예시:

python rpi_websocket_anc_server.py --gain 0.35 --delay-ms 80 --cutoff 200
"""

import argparse
import asyncio
from collections import deque
import time

import numpy as np
import websockets


class FixedGainDelayDSP:
    """
    Fixed Gain/Delay 방식으로 반대 위상 신호를 만드는 클래스입니다.

    Fixed Gain/Delay를 쉽게 말하면:

    1. 마이크로 들어온 소리를 받는다.
    2. 필요한 저주파만 남긴다.
    3. 소리를 조금 늦춘다.
    4. 세기를 조절한다.
    5. 부호를 반대로 바꾼다.
    6. 그 결과를 스피커로 내보낸다.

    그래서 핵심은 아래입니다.

    anti = -gain * delayed
    """

    def __init__(self, fs=1000, cutoff=200.0, gain=0.35, delay_ms=80.0, output_limit=18000):
        # fs:
        # ESP32가 라즈베리파이로 보내는 샘플링 주파수입니다.
        # ESP32 코드의 SEND_SAMPLE_RATE와 같아야 합니다.
        self.fs = int(fs)

        # cutoff:
        # 저역통과필터 기준 주파수입니다.
        # 이 값보다 낮은 주파수 성분을 중심으로 사용합니다.
        #
        # 예:
        # 200Hz면 200Hz 이하 저주파 성분을 중심으로 처리합니다.
        self.cutoff = float(cutoff)

        # gain:
        # 반대 위상 소리를 얼마나 크게 만들지 정하는 값입니다.
        #
        # 너무 작으면 효과가 약하고,
        # 너무 크면 하울링이나 소리 증가가 생길 수 있습니다.
        self.gain = float(gain)

        # delay_ms:
        # 반대 위상 신호를 몇 ms 늦출지 정하는 값입니다.
        #
        # 통신 지연, 계산 지연, 스피커 출력 지연을 고려해 조절합니다.
        self.delay_ms = float(delay_ms)

        # output_limit:
        # 출력이 너무 커지지 않도록 제한하는 값입니다.
        self.output_limit = int(output_limit)

        # delay 시간을 샘플 개수로 변환합니다.
        # 예: fs=1000, delay=80ms면 80샘플 지연입니다.
        self.delay_samples = max(int(round(self.fs * self.delay_ms / 1000.0)), 1)

        # delay_buffer:
        # 과거 소리 데이터를 저장해두는 공간입니다.
        # 여기에서 오래된 값을 꺼내면 "지연된 소리"가 됩니다.
        self.delay_buffer = deque([0.0] * self.delay_samples, maxlen=self.delay_samples)

        # 1차 저역통과필터 계산을 위한 alpha 값입니다.
        # 필터를 간단하게 만들기 위해 scipy 대신 직접 계산합니다.
        dt = 1.0 / self.fs
        rc = 1.0 / (2.0 * np.pi * self.cutoff)
        self.lpf_alpha = dt / (rc + dt)

        # 필터의 이전 상태값입니다.
        self.lpf_state = 0.0

        # DC offset 제거용 변수입니다.
        # 마이크 신호가 0을 중심으로 흔들리도록 보정합니다.
        self.dc = 0.0
        self.dc_alpha = 0.001

        # 상태 확인용 값입니다.
        self.last_mic_rms = 0.0
        self.last_out_rms = 0.0
        self.last_mic_peak = 0.0
        self.last_out_peak = 0.0

    def process_frame(self, int16_frame):
        """
        ESP32에서 받은 마이크 프레임 1개를 처리합니다.

        입력:
        - int16_frame
        - ESP32가 보낸 마이크 소리 데이터

        출력:
        - int16 상쇄 프레임
        - ESP32가 스피커로 출력할 반대 위상 소리 데이터
        """

        # 계산하기 쉽게 float으로 바꿉니다.
        x = int16_frame.astype(np.float32)

        # =================================================
        # 1. DC offset 제거
        # =================================================
        #
        # 마이크 입력은 0을 중심으로 예쁘게 흔들리지 않고
        # 한쪽으로 치우칠 수 있습니다.
        #
        # 이 치우침을 제거해서 신호 중심을 0 근처로 맞춥니다.

        block_mean = float(np.mean(x))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = x - self.dc

        # 출력 배열 준비
        out = np.zeros_like(x, dtype=np.float32)

        # 샘플 하나씩 처리합니다.
        for i, sample in enumerate(x):

            # =================================================
            # 2. 저역통과필터
            # =================================================
            #
            # 저역통과필터는 낮은 주파수는 남기고
            # 높은 주파수는 줄이는 필터입니다.
            #
            # 층간소음은 쿵쿵, 웅웅 같은 저주파 성분이 중요하기 때문에
            # 저주파 위주로 처리합니다.

            self.lpf_state = self.lpf_state + self.lpf_alpha * (sample - self.lpf_state)
            filtered = self.lpf_state

            # =================================================
            # 3. delay 적용
            # =================================================
            #
            # 반대 위상 소리는 타이밍이 맞아야 효과가 있습니다.
            # 그래서 필터된 소리를 delay_buffer에 저장했다가
            # 일정 시간 지난 값을 꺼냅니다.

            delayed = self.delay_buffer[0]
            self.delay_buffer.append(filtered)

            # =================================================
            # 4. 반대 위상 신호 생성
            # =================================================
            #
            # 이 코드에서 가장 중요한 부분입니다.
            #
            # 원래 소리와 반대 방향의 소리를 만들기 위해
            # -gain을 곱합니다.
            #
            # 예:
            # 원래 소리:      + + + +
            # 반대 위상 소리: - - - -
            #
            # 둘이 특정 위치에서 만나면 소리가 줄어들 수 있습니다.

            anti = -self.gain * delayed

            # =================================================
            # 5. 출력 제한
            # =================================================
            #
            # 계산된 상쇄 소리가 너무 커지면
            # 스피커가 찢어지는 소리, 하울링, 왜곡이 생길 수 있습니다.
            #
            # 그래서 최대/최소값을 제한합니다.

            if anti > self.output_limit:
                anti = self.output_limit
            elif anti < -self.output_limit:
                anti = -self.output_limit

            out[i] = anti

        # =====================================================
        # 6. 상태 확인용 값 계산
        # =====================================================
        #
        # micRMS:
        # - 들어온 마이크 소리의 평균적인 크기
        #
        # outRMS:
        # - 출력할 상쇄 소리의 평균적인 크기
        #
        # peak:
        # - 순간적으로 가장 큰 값

        self.last_mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))
        self.last_out_rms = float(np.sqrt(np.mean(out ** 2) + 1e-12))
        self.last_mic_peak = float(np.max(np.abs(x)))
        self.last_out_peak = float(np.max(np.abs(out)))

        # ESP32로 보내기 위해 int16 형식으로 바꿉니다.
        return out.astype(np.int16)


async def anc_handler(websocket, dsp, stats):
    """
    ESP32가 WebSocket으로 연결되면 실행되는 함수입니다.

    하는 일:
    1. ESP32에서 마이크 프레임을 받는다.
    2. dsp.process_frame()으로 상쇄 프레임을 만든다.
    3. 상쇄 프레임을 ESP32로 다시 보낸다.
    """

    client = websocket.remote_address
    print(f"[CONNECTED] ESP32 client: {client}")

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # int16 데이터는 2바이트 단위입니다.
                # 길이가 홀수면 정상 데이터가 아니므로 무시합니다.
                if len(message) % 2 != 0:
                    continue

                # ESP32에서 받은 binary 데이터를 int16 배열로 변환합니다.
                frame = np.frombuffer(message, dtype=np.int16)

                # 상쇄 신호 계산
                anti = dsp.process_frame(frame)

                # 계산된 상쇄 신호를 ESP32로 다시 보냅니다.
                await websocket.send(anti.tobytes())

                stats["frames"] += 1
                stats["samples"] += len(frame)

            else:
                print("[TEXT]", message)

    except websockets.exceptions.ConnectionClosed:
        print(f"[DISCONNECTED] ESP32 client: {client}")


async def monitor(dsp, stats):
    """
    1초마다 상태를 출력하는 함수입니다.

    이 출력으로 확인할 수 있는 것:
    - ESP32에서 데이터가 들어오고 있는지
    - 마이크 소리가 감지되는지
    - 상쇄 출력값이 만들어지는지
    """

    last_frames = 0
    last_time = time.time()

    while True:
        await asyncio.sleep(1.0)

        now = time.time()
        frames_now = stats["frames"]
        diff_frames = frames_now - last_frames
        dt = now - last_time

        fps = diff_frames / dt if dt > 0 else 0.0

        last_frames = frames_now
        last_time = now

        print(
            f"frames/s={fps:.2f} | "
            f"totalFrames={stats['frames']} | "
            f"samples={stats['samples']} | "
            f"micRMS={dsp.last_mic_rms:.1f} | "
            f"outRMS={dsp.last_out_rms:.1f} | "
            f"micPeak={dsp.last_mic_peak:.1f} | "
            f"outPeak={dsp.last_out_peak:.1f}"
        )


async def main_async(args):
    """
    WebSocket 서버를 시작하는 함수입니다.
    """

    dsp = FixedGainDelayDSP(
        fs=args.fs,
        cutoff=args.cutoff,
        gain=args.gain,
        delay_ms=args.delay_ms,
        output_limit=args.output_limit,
    )

    stats = {"frames": 0, "samples": 0}

    print("=" * 80)
    print("Raspberry Pi WebSocket ANC Server")
    print("=" * 80)
    print(f"host         : {args.host}")
    print(f"port         : {args.port}")
    print(f"fs           : {args.fs} Hz")
    print(f"cutoff       : {args.cutoff} Hz")
    print(f"gain         : {args.gain}")
    print(f"delay        : {args.delay_ms} ms")
    print(f"output limit : {args.output_limit}")
    print("=" * 80)
    print("ESP32 코드의 RPI_HOST를 이 라즈베리파이 IP로 맞추세요.")
    print("=" * 80)

    async def handler(websocket):
        await anc_handler(websocket, dsp, stats)

    # WebSocket 서버를 시작합니다.
    # ESP32는 이 서버에 접속해서 마이크 데이터를 보냅니다.
    async with websockets.serve(handler, args.host, args.port, max_size=2**20):
        await monitor(dsp, stats)


def main():
    """
    프로그램 시작점입니다.
    실행 옵션을 읽고 서버를 시작합니다.
    """

    parser = argparse.ArgumentParser()

    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)

    # ESP32 코드의 SEND_SAMPLE_RATE와 같아야 합니다.
    parser.add_argument("--fs", type=int, default=1000)

    parser.add_argument("--cutoff", type=float, default=200.0)
    parser.add_argument("--gain", type=float, default=0.35)
    parser.add_argument("--delay-ms", type=float, default=80.0)
    parser.add_argument("--output-limit", type=int, default=18000)

    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n사용자 중지")


if __name__ == "__main__":
    main()
