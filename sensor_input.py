"""
=============================================================
  층간소음 ANC 프로젝트 - 센서 & 입력 담당 (Raspberry Pi Only)
  파일명: sensor_input.py
  설명: 라즈베리파이만 사용하는 버전
        아두이노/진동센서 없이 I2S 마이크만으로 동작
=============================================================
  [사전 설치]
  pip install sounddevice numpy RPi.GPIO

  [라즈베리파이 I2S 설정] /boot/config.txt 에 아래 추가:
  dtoverlay=i2s-mmap
  dtoverlay=googlevoicehat-soundcard   (또는 사용하는 HAT에 맞게)
=============================================================
"""

import numpy as np
import sounddevice as sd
import wave
import time
import threading
import os


# 라즈베리파이 GPIO (진동 감지용 - 선택사항)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[경고] RPi.GPIO 미설치 - GPIO 진동 감지 비활성화")

# ─────────────────────────────────────────────
#  전역 설정값
# ─────────────────────────────────────────────
SAMPLE_RATE      = 48000   # I2S 친화적 샘플레이트
CHANNELS         = 1       # 모노
BLOCK_SIZE       = 128     # 2.7ms 딜레이
DTYPE            = 'float32'
NOISE_FLOOR      = 0.01    # 노이즈 게이트 임계값
SAVE_DIR         = './noise_samples'

# 라즈베리파이 GPIO 핀 번호 (진동 센서 직접 연결 시)
VIBRATION_PIN    = 17      # BCM 기준 GPIO 17번 핀


# ─────────────────────────────────────────────
#  1. 신호 안정화 클래스
# ─────────────────────────────────────────────
class SignalStabilizer:
    """
    마이크 원시 신호 전처리 클래스
    DC 오프셋 제거 → 노이즈 게이트
    """

    def __init__(self, noise_floor: float = NOISE_FLOOR):
        self.noise_floor = noise_floor

    def remove_dc_offset(self, signal: np.ndarray) -> np.ndarray:
        """DC 오프셋 제거: 신호 평균을 0으로 이동"""
        return signal - np.mean(signal)

    def apply_noise_gate(self, signal: np.ndarray) -> np.ndarray:
        """노이즈 게이트: RMS가 임계값 이하면 무음 처리"""
        rms = np.sqrt(np.mean(signal ** 2))
        if rms < self.noise_floor:
            return np.zeros_like(signal)
        return signal

    def process(self, raw_signal: np.ndarray) -> np.ndarray:
        """전체 안정화 파이프라인"""
        signal = self.remove_dc_offset(raw_signal)
        signal = self.apply_noise_gate(signal)
        return signal


# ─────────────────────────────────────────────
#  2. GPIO 진동 감지 (라즈베리파이 직접 연결)
# ─────────────────────────────────────────────
class GPIOVibrationDetector:
    """
    라즈베리파이 GPIO에 진동 센서를 직접 연결하는 클래스
    아두이노 없이 라즈베리파이만으로 진동 감지
    SW-420 같은 디지털 진동 센서 사용 시 활용

    [연결 방법]
    진동 센서 VCC  → 라즈베리파이 3.3V (1번 핀)
    진동 센서 GND  → 라즈베리파이 GND  (6번 핀)
    진동 센서 DO   → 라즈베리파이 GPIO17 (11번 핀)
    """

    def __init__(self, pin: int = VIBRATION_PIN):
        self.pin = pin
        self.detected = False

        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            # 진동 감지 시 자동으로 콜백 호출 (인터럽트 방식)
            GPIO.add_event_detect(
                self.pin,
                GPIO.RISING,
                callback=self._on_vibration,
                bouncetime=100   # 100ms 디바운스 (중복 감지 방지)
            )
            print(f"[GPIO 진동 감지] GPIO{self.pin} 핀 설정 완료")
        else:
            print("[GPIO 진동 감지] 비활성화 상태")

    def _on_vibration(self, channel):
        """진동 감지 시 자동 호출되는 인터럽트 콜백"""
        self.detected = True
        print(f"[진동 감지] GPIO{channel} 신호 감지!")

    def is_detected(self) -> bool:
        """진동 감지 여부 반환 후 초기화"""
        if self.detected:
            self.detected = False  # 읽은 후 초기화
            return True
        return False

    def cleanup(self):
        """GPIO 핀 정리"""
        if GPIO_AVAILABLE:
            GPIO.cleanup()
            print("[GPIO] 핀 정리 완료")


# ─────────────────────────────────────────────
#  3. 노이즈 데이터 수집기
# ─────────────────────────────────────────────
class NoiseDataCollector:
    """
    층간소음 샘플을 .wav 파일로 저장
    AI러닝팀에 학습 데이터 제공용
    """

    def __init__(self, save_dir: str = SAVE_DIR):
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def collect(self, duration: float = 5.0, label: str = "sample") -> np.ndarray:
        """duration초 동안 마이크 녹음 후 WAV로 저장"""
        print(f"[수집] {duration}초간 녹음 시작... (레이블: {label})")
        recording = sd.rec(
            int(duration * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE
        )
        sd.wait()

        filename = os.path.join(
            self.save_dir,
            f"{label}_{int(time.time())}.wav"
        )
        self._save_wav(recording, filename)
        print(f"[저장 완료] {filename}")
        return recording

    def _save_wav(self, data: np.ndarray, filename: str):
        """float32 배열을 16-bit WAV 파일로 저장"""
        audio_int16 = np.clip(data * 32767, -32768, 32767).astype(np.int16)
        with wave.open(filename, 'w') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_int16.tobytes())


# ─────────────────────────────────────────────
#  4. 메인 입력 파이프라인
# ─────────────────────────────────────────────
class SensorInputPipeline:
    """
    라즈베리파이 전용 센서 & 입력 핵심 클래스
    마이크 스트림 → 안정화 → DSP팀 콜백 직접 호출
    """

    def __init__(self):
        self.stabilizer   = SignalStabilizer()
        self.vibration    = GPIOVibrationDetector()  # 아두이노 대신 GPIO 직접 연결
        self.collector    = NoiseDataCollector()
        self.dsp_callback = None
        self._running     = False
        self._stream      = None

    # ── DSP팀 콜백 등록 ───────────────────────
    def set_dsp_callback(self, func):
        """
        DSP팀이 자신의 처리 함수를 등록하는 메서드

        사용법 (DSP팀):
            from sensor_input import SensorInputPipeline
            pipeline = SensorInputPipeline()
            pipeline.set_dsp_callback(내_처리_함수)
            pipeline.start()
        """
        self.dsp_callback = func
        print("[DSP 연동] 콜백 함수 등록 완료")

    # ── 내부 오디오 콜백 ──────────────────────
    def _audio_callback(self, indata, frames, time_info, status):
        """
        sounddevice가 BLOCK_SIZE마다 자동 호출
        원시 신호 → 안정화 → DSP팀 함수 직접 호출 (큐 없음)
        """
        if status:
            print(f"[콜백 경고] {status}")

        raw    = indata[:, 0].copy()
        stable = self.stabilizer.process(raw)
        rms    = np.sqrt(np.mean(stable ** 2))

        if rms > NOISE_FLOOR and self.dsp_callback is not None:
            self.dsp_callback(stable)

    # ── 스트림 시작 / 중지 ────────────────────
    def start(self):
        """마이크 입력 스트림 시작"""
        if self._running:
            print("[경고] 이미 실행 중입니다.")
            return

        self._running = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            blocksize=BLOCK_SIZE,
            dtype=DTYPE,
            callback=self._audio_callback,
            latency='low'
        )
        self._stream.start()
        print(f"[시작] 마이크 스트림 ON")
        print(f"       샘플레이트 : {SAMPLE_RATE}Hz")
        print(f"       블록 크기  : {BLOCK_SIZE}")
        print(f"       딜레이     : {BLOCK_SIZE / SAMPLE_RATE * 1000:.1f}ms")

    def stop(self):
        """마이크 입력 스트림 중지"""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
        self.vibration.cleanup()  # GPIO 핀 정리
        print("[중지] 마이크 스트림 OFF")

    # ── 진동 모니터 스레드 ────────────────────
    def _vibration_monitor(self):
        """
        GPIO 진동 감지 상태를 주기적으로 확인하는 스레드
        GPIO 인터럽트가 감지하면 여기서 출력
        """
        while self._running:
            if self.vibration.is_detected():
                print("[층간소음 감지!] 진동 신호 확인됨")
            time.sleep(0.05)  # 50ms 간격 폴링

    def start_vibration_monitor(self):
        """진동 센서 모니터링 스레드 시작"""
        t = threading.Thread(target=self._vibration_monitor, daemon=True)
        t.start()
        print("[진동 모니터] 백그라운드 감시 시작")

    # ── 샘플 수집 편의 메서드 ─────────────────
    def collect_sample(self, duration: float = 5.0, label: str = "noise"):
        """AI러닝팀용 노이즈 샘플 수집"""
        return self.collector.collect(duration=duration, label=label)


# ─────────────────────────────────────────────
#  5. 실행 진입점
# ─────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  층간소음 ANC - 센서 & 입력 모듈")
    print("  Raspberry Pi Only 버전")
    print("=" * 55)

    pipeline = SensorInputPipeline()

    # DSP팀 콜백 등록 (실제 운영 시 DSP팀 함수로 교체)
    def dsp_process(signal):
        rms = np.sqrt(np.mean(signal ** 2))
        print(f"[DSP 수신] 블록 길이: {len(signal)}  RMS: {rms:.4f}")

    pipeline.set_dsp_callback(dsp_process)

    # 마이크 스트림 & 진동 모니터 시작
    pipeline.start()
    pipeline.start_vibration_monitor()

    print("\n[실행 중] Ctrl+C 로 종료\n")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[종료 요청]")
    finally:
        pipeline.stop()
        print("[종료 완료]")


if __name__ == "__main__":
    main()