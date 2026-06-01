
"""
Raspberry Pi WebSocket ANC Server

구조:
ESP32가 INMP441 마이크 샘플을 WebSocket binary로 전송
→ Raspberry Pi가 DC 제거, 저역통과필터, delay, -gain 처리
→ ESP32로 상쇄 샘플 반환
→ ESP32가 GPIO25 DAC로 출력
→ PAM8403
→ 스피커

설치:
pip install websockets numpy

실행:
python rpi_websocket_anc_server.py

예:
python rpi_websocket_anc_server.py --gain 0.35 --delay-ms 80 --cutoff 200
"""

import argparse
import asyncio
from collections import deque
import time

import numpy as np
import websockets


class FixedGainDelayDSP:
    def __init__(self, fs=1000, cutoff=200.0, gain=0.35, delay_ms=80.0, output_limit=18000):
        self.fs = int(fs)
        self.cutoff = float(cutoff)
        self.gain = float(gain)
        self.delay_ms = float(delay_ms)
        self.output_limit = int(output_limit)

        self.delay_samples = max(int(round(self.fs * self.delay_ms / 1000.0)), 1)
        self.delay_buffer = deque([0.0] * self.delay_samples, maxlen=self.delay_samples)

        dt = 1.0 / self.fs
        rc = 1.0 / (2.0 * np.pi * self.cutoff)
        self.lpf_alpha = dt / (rc + dt)

        self.lpf_state = 0.0
        self.dc = 0.0
        self.dc_alpha = 0.001

        self.last_mic_rms = 0.0
        self.last_out_rms = 0.0
        self.last_mic_peak = 0.0
        self.last_out_peak = 0.0

    def process_frame(self, int16_frame):
        x = int16_frame.astype(np.float32)

        block_mean = float(np.mean(x))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = x - self.dc

        out = np.zeros_like(x, dtype=np.float32)

        for i, sample in enumerate(x):
            self.lpf_state = self.lpf_state + self.lpf_alpha * (sample - self.lpf_state)
            filtered = self.lpf_state

            delayed = self.delay_buffer[0]
            self.delay_buffer.append(filtered)

            anti = -self.gain * delayed

            if anti > self.output_limit:
                anti = self.output_limit
            elif anti < -self.output_limit:
                anti = -self.output_limit

            out[i] = anti

        self.last_mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))
        self.last_out_rms = float(np.sqrt(np.mean(out ** 2) + 1e-12))
        self.last_mic_peak = float(np.max(np.abs(x)))
        self.last_out_peak = float(np.max(np.abs(out)))

        return out.astype(np.int16)


async def anc_handler(websocket, dsp, stats):
    client = websocket.remote_address
    print(f"[CONNECTED] ESP32 client: {client}")

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) % 2 != 0:
                    continue

                frame = np.frombuffer(message, dtype=np.int16)
                anti = dsp.process_frame(frame)

                await websocket.send(anti.tobytes())

                stats["frames"] += 1
                stats["samples"] += len(frame)
            else:
                print("[TEXT]", message)

    except websockets.exceptions.ConnectionClosed:
        print(f"[DISCONNECTED] ESP32 client: {client}")


async def monitor(dsp, stats):
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

    async with websockets.serve(handler, args.host, args.port, max_size=2**20):
        await monitor(dsp, stats)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
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
