import argparse
import asyncio
import time
import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi
import websockets

class WaveformANCProcessor:
    def __init__(self, fs=1000, gain=0.25, cutoff=250.0, 
                 latency_ms=120.0, output_limit=14000, min_rms=250.0):
        self.fs = int(fs)
        self.gain = float(gain)
        self.cutoff = float(cutoff)
        self.latency_ms = float(latency_ms)
        self.output_limit = int(output_limit)
        self.min_rms = float(min_rms)

        # DC 제거
        self.dc = 0.0
        self.dc_alpha = 0.008

        # Low-pass Filter (4차 Butterworth)
        nyquist = self.fs / 2.0
        self.sos = butter(4, self.cutoff / nyquist, btype='low', output='sos')
        self.zi = sosfilt_zi(self.sos) * 0.0

        # Delay buffer (최대 300ms까지 지원)
        self.max_delay_samples = int(self.fs * 0.3)
        self.delay_buffer = np.zeros(self.max_delay_samples, dtype=np.float32)
        self.delay_samples = int(round(self.fs * self.latency_ms / 1000.0))
        self.delay_samples = max(1, min(self.delay_samples, self.max_delay_samples - 1))

        # 통계
        self.last_mic_rms = 0.0
        self.last_out_rms = 0.0
        self.last_mic_peak = 0.0
        self.last_out_peak = 0.0

    def process_frame(self, int16_frame):
        x = int16_frame.astype(np.float32)

        # 1. DC 제거
        block_mean = float(np.mean(x))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = x - self.dc

        mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))
        mic_peak = float(np.max(np.abs(x)))

        # 2. 신호가 너무 작으면 출력 0
        if mic_rms < self.min_rms:
            out = np.zeros_like(x, dtype=np.float32)
            self.last_mic_rms = mic_rms
            self.last_out_rms = 0.0
            self.last_mic_peak = mic_peak
            self.last_out_peak = 0.0
            return out.astype(np.int16)

        # 3. Low-pass Filter
        filtered, self.zi = sosfilt(self.sos, x, zi=self.zi)

        # 4. Delay 적용 (waveform 기반)
        delayed = np.zeros_like(filtered)
        for i in range(len(filtered)):
            delayed[i] = self.delay_buffer[-self.delay_samples]
            # 버퍼 업데이트
            self.delay_buffer = np.roll(self.delay_buffer, -1)
            self.delay_buffer[-1] = filtered[i]

        # 5. 반대 위상 + Gain
        anti = -self.gain * delayed
        anti = np.clip(anti, -self.output_limit, self.output_limit)

        # 통계 업데이트
        self.last_mic_rms = mic_rms
        self.last_out_rms = float(np.sqrt(np.mean(anti ** 2) + 1e-12))
        self.last_mic_peak = mic_peak
        self.last_out_peak = float(np.max(np.abs(anti)))

        return anti.astype(np.int16)


async def anc_handler(websocket, processor, stats):
    client = websocket.remote_address
    print(f"[CONNECTED] ESP32: {client}")

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) % 2 != 0:
                    continue

                frame = np.frombuffer(message, dtype=np.int16)
                anti = processor.process_frame(frame)

                await websocket.send(anti.tobytes())
                stats["frames"] += 1
                stats["samples"] += len(frame)
            else:
                print("[TEXT]", message)

    except websockets.exceptions.ConnectionClosed:
        print(f"[DISCONNECTED] ESP32: {client}")


async def monitor(processor, stats):
    last_frames = 0
    last_time = time.time()

    while True:
        await asyncio.sleep(1.0)
        now = time.time()
        diff = stats["frames"] - last_frames
        dt = now - last_time
        fps = diff / dt if dt > 0 else 0.0
        last_frames = stats["frames"]
        last_time = now

        print(f"fps={fps:.1f} | "
              f"micRMS={processor.last_mic_rms:.1f} | "
              f"outRMS={processor.last_out_rms:.1f} | "
              f"micPeak={processor.last_mic_peak:.1f} | "
              f"outPeak={processor.last_out_peak:.1f}")


async def main_async(args):
    processor = WaveformANCProcessor(
        fs=args.fs,
        gain=args.gain,
        cutoff=args.cutoff,
        latency_ms=args.latency_ms,
        output_limit=args.output_limit,
        min_rms=args.min_rms
    )

    stats = {"frames": 0, "samples": 0}

    print("=" * 70)
    print("Raspberry Pi - Waveform 기반 ANC (B-2 버전)")
    print("=" * 70)
    print(f"fs            : {args.fs} Hz")
    print(f"gain          : {args.gain}")
    print(f"cutoff        : {args.cutoff} Hz")
    print(f"latency       : {args.latency_ms} ms")
    print(f"output_limit  : {args.output_limit}")
    print("=" * 70)

    async def handler(websocket):
        await anc_handler(websocket, processor, stats)

    async with websockets.serve(handler, args.host, args.port, max_size=2**20):
        await monitor(processor, stats)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fs", type=int, default=1000)
    parser.add_argument("--gain", type=float, default=0.22)
    parser.add_argument("--cutoff", type=float, default=280.0)
    parser.add_argument("--latency-ms", type=float, default=125.0)
    parser.add_argument("--output-limit", type=int, default=14000)
    parser.add_argument("--min-rms", type=float, default=280.0)
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n사용자 중지")


if __name__ == "__main__":
    main()
