"""
rpi_websocket_destructive_interference_server.py

상쇄간섭에 더 가깝게 업그레이드한 Raspberry Pi 서버 코드입니다.

기존 방식:
마이크 입력 -> 저역통과필터 -> delay -> -gain -> 출력

업그레이드 방식:
마이크 입력 -> FFT로 주요 주파수 찾기 -> 위상 추정 -> 같은 주파수의 사인파 생성
-> 180도 반대 위상 적용 -> WebSocket 지연 보정 -> ESP32로 전송

설치:
pip install websockets numpy

실행:
python rpi_websocket_destructive_interference_server.py

예:
python rpi_websocket_destructive_interference_server.py --gain 0.45 --latency-ms 140 --min-freq 30 --max-freq 400
"""

import argparse
import asyncio
import time
import numpy as np
import websockets


class DestructiveInterferenceDSP:
    def __init__(self, fs=1000, gain=0.45, min_freq=30.0, max_freq=400.0,
                 latency_ms=140.0, output_limit=18000, min_rms=300.0):
        self.fs = int(fs)
        self.gain = float(gain)
        self.min_freq = float(min_freq)
        self.max_freq = float(max_freq)
        self.latency_ms = float(latency_ms)
        self.output_limit = int(output_limit)
        self.min_rms = float(min_rms)

        self.dc = 0.0
        self.dc_alpha = 0.005

        self.prev_freq = 100.0
        self.prev_amp = 0.0

        self.last_freq = 0.0
        self.last_amp = 0.0
        self.last_phase = 0.0
        self.last_mic_rms = 0.0
        self.last_out_rms = 0.0
        self.last_mic_peak = 0.0
        self.last_out_peak = 0.0

    def find_dominant_frequency_and_phase(self, x):
        n = len(x)

        # 창 함수를 곱해서 FFT 분석이 튀는 것을 줄입니다.
        window = np.hanning(n)
        xw = x * window

        spectrum = np.fft.rfft(xw)
        freqs = np.fft.rfftfreq(n, 1.0 / self.fs)

        mask = (freqs >= self.min_freq) & (freqs <= self.max_freq)

        if not np.any(mask):
            return self.prev_freq, 0.0, 0.0

        masked_spectrum = spectrum[mask]
        masked_freqs = freqs[mask]

        idx = int(np.argmax(np.abs(masked_spectrum)))

        dominant_freq = float(masked_freqs[idx])
        complex_value = masked_spectrum[idx]

        # 복소수 각도가 해당 주파수의 위상입니다.
        phase = float(np.angle(complex_value))

        # 대략적인 진폭 추정값입니다.
        amp = float(2.0 * np.abs(complex_value) / max(np.sum(window), 1e-9))

        return dominant_freq, amp, phase

    def process_frame(self, int16_frame):
        x = int16_frame.astype(np.float32)

        # DC offset 제거
        block_mean = float(np.mean(x))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = x - self.dc

        mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))
        mic_peak = float(np.max(np.abs(x)))

        if mic_rms < self.min_rms:
            out = np.zeros_like(x, dtype=np.float32)
            self.last_mic_rms = mic_rms
            self.last_out_rms = 0.0
            self.last_mic_peak = mic_peak
            self.last_out_peak = 0.0
            return out.astype(np.int16)

        # 가장 강한 주파수와 위상 찾기
        freq, amp, phase = self.find_dominant_frequency_and_phase(x)

        # 주파수와 진폭이 너무 급하게 바뀌지 않게 부드럽게 추적
        smooth = 0.75
        freq = smooth * self.prev_freq + (1.0 - smooth) * freq
        amp = smooth * self.prev_amp + (1.0 - smooth) * amp

        self.prev_freq = freq
        self.prev_amp = amp

        # WebSocket 왕복 지연 + 출력 지연 + 스피커 전달 지연 보정
        latency_sec = self.latency_ms / 1000.0
        phase_correction = -2.0 * np.pi * freq * latency_sec

        # +pi가 180도 반대 위상입니다.
        anti_phase = phase + np.pi + phase_correction

        n = len(x)
        t = np.arange(n) / self.fs

        anti = self.gain * amp * np.sin(2.0 * np.pi * freq * t + anti_phase)
        anti = np.clip(anti, -self.output_limit, self.output_limit)

        self.last_freq = float(freq)
        self.last_amp = float(amp)
        self.last_phase = float(phase)
        self.last_mic_rms = mic_rms
        self.last_out_rms = float(np.sqrt(np.mean(anti ** 2) + 1e-12))
        self.last_mic_peak = mic_peak
        self.last_out_peak = float(np.max(np.abs(anti)))

        return anti.astype(np.int16)


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
            f"freq={dsp.last_freq:.1f}Hz | "
            f"amp={dsp.last_amp:.1f} | "
            f"micRMS={dsp.last_mic_rms:.1f} | "
            f"outRMS={dsp.last_out_rms:.1f} | "
            f"micPeak={dsp.last_mic_peak:.1f} | "
            f"outPeak={dsp.last_out_peak:.1f}"
        )


async def main_async(args):
    dsp = DestructiveInterferenceDSP(
        fs=args.fs,
        gain=args.gain,
        min_freq=args.min_freq,
        max_freq=args.max_freq,
        latency_ms=args.latency_ms,
        output_limit=args.output_limit,
        min_rms=args.min_rms,
    )

    stats = {"frames": 0, "samples": 0}

    print("=" * 80)
    print("Raspberry Pi Destructive Interference WebSocket Server")
    print("=" * 80)
    print(f"host         : {args.host}")
    print(f"port         : {args.port}")
    print(f"fs           : {args.fs} Hz")
    print(f"gain         : {args.gain}")
    print(f"freq range   : {args.min_freq} ~ {args.max_freq} Hz")
    print(f"latency      : {args.latency_ms} ms")
    print(f"output limit : {args.output_limit}")
    print(f"min RMS      : {args.min_rms}")
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
    parser.add_argument("--gain", type=float, default=0.45)
    parser.add_argument("--min-freq", type=float, default=30.0)
    parser.add_argument("--max-freq", type=float, default=400.0)
    parser.add_argument("--latency-ms", type=float, default=140.0)
    parser.add_argument("--output-limit", type=int, default=18000)
    parser.add_argument("--min-rms", type=float, default=300.0)
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n사용자 중지")


if __name__ == "__main__":
    main()
