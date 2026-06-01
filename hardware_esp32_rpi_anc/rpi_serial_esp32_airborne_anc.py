"""
rpi_serial_esp32_airborne_anc.py

부품 구조:
INMP441 마이크 -> ESP32 -> USB Serial -> Raspberry Pi -> PAM8403 -> Speaker

Raspberry Pi 역할:
- ESP32가 Serial로 보내는 마이크 샘플을 읽음
- DC offset 제거
- 저역통과필터 적용
- delay 적용
- -gain 곱해서 반대 위상 신호 생성
- 라즈베리파이 오디오 출력으로 내보냄
- PAM8403이 증폭해서 스피커로 출력

주의:
- 완전 상쇄 목적이 아니라 일부 저감 가능성 확인용 프로토타입 코드입니다.
- ESP32 -> Raspberry Pi 통신 지연 때문에 순간 충격음은 어렵고, 지속 저주파 소리에 더 적합합니다.
- 처음에는 스피커 볼륨을 낮게 시작하세요.

설치:
pip install numpy scipy sounddevice pyserial

장치 확인:
python rpi_serial_esp32_airborne_anc.py --list-audio-devices

실행 예시:
python rpi_serial_esp32_airborne_anc.py --serial-port /dev/ttyUSB0 --output-device 0

gain/delay 조절:
python rpi_serial_esp32_airborne_anc.py --serial-port /dev/ttyUSB0 --gain 0.25 --delay-ms 60 --cutoff 180
"""

import argparse
import queue
import sys
import threading
import time
from collections import deque

import numpy as np
import serial
import sounddevice as sd
from scipy.signal import butter, sosfilt, sosfilt_zi


class SerialMicReader:
    """
    ESP32가 보내는 마이크 샘플을 Serial로 읽어서 queue에 저장.
    ESP32는 한 줄에 정수 샘플 하나씩 보낸다.
    """

    def __init__(self, port, baudrate=921600, max_queue=20000):
        self.port = port
        self.baudrate = baudrate
        self.q = queue.Queue(maxsize=max_queue)
        self.running = False
        self.thread = None
        self.ser = None

        self.received_count = 0
        self.skipped_count = 0
        self.last_value = 0

    def start(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
        time.sleep(2.0)

        # ESP32 리셋 후 쌓인 데이터 조금 비우기
        self.ser.reset_input_buffer()

        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.ser is not None:
            self.ser.close()

    def _read_loop(self):
        while self.running:
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()

                if not line:
                    continue

                # ESP32 상태 메시지는 #으로 시작하므로 무시
                if line.startswith("#"):
                    continue

                value = int(line)
                self.last_value = value

                if self.q.full():
                    try:
                        self.q.get_nowait()
                        self.skipped_count += 1
                    except queue.Empty:
                        pass

                self.q.put_nowait(value)
                self.received_count += 1

            except ValueError:
                continue
            except Exception as e:
                print("Serial read error:", e, file=sys.stderr)
                time.sleep(0.1)

    def get_samples(self, n):
        """
        n개 샘플을 가져온다.
        부족하면 마지막 값을 반복해서 채운다.
        """
        samples = []

        for _ in range(n):
            try:
                value = self.q.get_nowait()
            except queue.Empty:
                value = self.last_value

            samples.append(value)

        return np.array(samples, dtype=np.float32)


class FixedGainDelayProcessor:
    """
    Fixed Gain/Delay 방식 처리기.

    핵심:
    anti_noise = -gain * delayed_signal
    """

    def __init__(
        self,
        fs=1000,
        cutoff=180.0,
        gain=0.25,
        delay_ms=60.0,
        input_scale=5000.0,
        output_limit=0.5,
    ):
        self.fs = int(fs)
        self.cutoff = float(cutoff)
        self.gain = float(gain)
        self.delay_ms = float(delay_ms)
        self.input_scale = float(input_scale)
        self.output_limit = float(output_limit)

        self.delay_samples = int(round(self.fs * self.delay_ms / 1000.0))
        self.delay_samples = max(self.delay_samples, 1)

        self.delay_buffer = deque([0.0] * self.delay_samples, maxlen=self.delay_samples)

        nyquist = self.fs / 2.0
        safe_cutoff = min(self.cutoff, nyquist * 0.9)

        self.sos = butter(
            N=4,
            Wn=safe_cutoff / nyquist,
            btype="low",
            output="sos",
        )
        self.zi = sosfilt_zi(self.sos) * 0.0

        self.dc = 0.0
        self.dc_alpha = 0.001

        self.mic_rms = 0.0
        self.out_rms = 0.0
        self.mic_peak = 0.0
        self.out_peak = 0.0

    def process(self, raw_samples):
        # 정규화 전 DC 제거
        raw = raw_samples.astype(np.float32)

        # DC offset 추정 및 제거
        # 블록 평균을 사용해서 천천히 보정
        block_mean = float(np.mean(raw))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = raw - self.dc

        # 크기 정규화
        x = x / self.input_scale

        # 너무 큰 값 제한
        x = np.clip(x, -2.0, 2.0)

        # 저역통과필터
        filtered, self.zi = sosfilt(self.sos, x, zi=self.zi)

        # delay
        delayed = np.zeros_like(filtered)

        for i, sample in enumerate(filtered):
            delayed[i] = self.delay_buffer[0]
            self.delay_buffer.append(sample)

        # 반대 위상
        anti_noise = -self.gain * delayed

        # 출력 제한
        anti_noise = np.clip(anti_noise, -self.output_limit, self.output_limit)

        self.mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))
        self.out_rms = float(np.sqrt(np.mean(anti_noise ** 2) + 1e-12))
        self.mic_peak = float(np.max(np.abs(x)))
        self.out_peak = float(np.max(np.abs(anti_noise)))

        # sounddevice 출력 shape: (frames, channels)
        return anti_noise.reshape(-1, 1).astype(np.float32)


def list_audio_devices():
    print(sd.query_devices())


def main():
    parser = argparse.ArgumentParser(
        description="ESP32 Serial Mic -> Raspberry Pi -> PAM8403 Speaker ANC prototype"
    )

    parser.add_argument("--list-audio-devices", action="store_true")
    parser.add_argument("--serial-port", type=str, default="/dev/ttyUSB0")
    parser.add_argument("--baudrate", type=int, default=921600)

    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--fs", type=int, default=1000)
    parser.add_argument("--block-size", type=int, default=64)

    parser.add_argument("--cutoff", type=float, default=180.0)
    parser.add_argument("--gain", type=float, default=0.25)
    parser.add_argument("--delay-ms", type=float, default=60.0)
    parser.add_argument("--input-scale", type=float, default=5000.0)
    parser.add_argument("--output-limit", type=float, default=0.5)

    args = parser.parse_args()

    if args.list_audio_devices:
        list_audio_devices()
        return

    print("=" * 80)
    print("ESP32 -> Raspberry Pi -> PAM8403 공기 중 층간소음 저감 프로토타입")
    print("=" * 80)
    print("완전 상쇄 목적이 아니라 일부 저감 가능성 확인용입니다.")
    print(f"serial port   : {args.serial_port}")
    print(f"baudrate      : {args.baudrate}")
    print(f"output device : {args.output_device}")
    print(f"fs            : {args.fs} Hz")
    print(f"block size    : {args.block_size}")
    print(f"cutoff        : {args.cutoff} Hz")
    print(f"gain          : {args.gain}")
    print(f"delay         : {args.delay_ms} ms")
    print(f"input scale   : {args.input_scale}")
    print(f"output limit  : {args.output_limit}")
    print("=" * 80)
    print("중지: Ctrl + C")
    print("처음에는 스피커 볼륨을 낮게 시작하세요.")
    print("=" * 80)

    reader = SerialMicReader(args.serial_port, args.baudrate)
    processor = FixedGainDelayProcessor(
        fs=args.fs,
        cutoff=args.cutoff,
        gain=args.gain,
        delay_ms=args.delay_ms,
        input_scale=args.input_scale,
        output_limit=args.output_limit,
    )

    reader.start()

    def callback(outdata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)

        try:
            raw = reader.get_samples(frames)
            y = processor.process(raw)

            if outdata.shape[1] == 1:
                outdata[:] = y
            else:
                outdata[:] = np.tile(y, (1, outdata.shape[1]))

        except Exception as e:
            print("audio callback error:", e, file=sys.stderr)
            outdata[:] = 0

    try:
        with sd.OutputStream(
            samplerate=args.fs,
            blocksize=args.block_size,
            dtype="float32",
            channels=1,
            device=args.output_device,
            callback=callback,
        ):
            last_print = time.time()

            while True:
                now = time.time()

                if now - last_print >= 1.0:
                    last_print = now

                    print(
                        f"serial received={reader.received_count} | "
                        f"queue={reader.q.qsize()} | "
                        f"skipped={reader.skipped_count} | "
                        f"mic RMS={processor.mic_rms:.5f} | "
                        f"out RMS={processor.out_rms:.5f} | "
                        f"mic peak={processor.mic_peak:.5f} | "
                        f"out peak={processor.out_peak:.5f}"
                    )

                time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n사용자 중지")

    finally:
        reader.stop()


if __name__ == "__main__":
    main()
