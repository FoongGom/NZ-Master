"""
sensor_input.py

담당자: 이형규
역할: 센서 & 입력

=====================================
핵심 목표: 전체 파이프라인 레이턴시 0.3ms 이하
=====================================

층간소음 능동 소음 제거(ANC) 흐름:
  마이크 입력
    → [sensor_input.py] 전처리 (0.3ms 이내)
    → 메인 제어 코드 (hybrid_control)
    → 스피커 출력 (역위상 신호로 소음 상쇄)

[변경 내역 - 하이브리드 코드 연동]
  1. sensitivity_scale 0.05 → 1.0
     - 기존 0.05 배율이 신호를 너무 작게 만들어
       하이브리드 코드의 분류 임계값과 맞지 않는 문제 수정
     - 하이브리드 코드의 generate_* 함수와 동일한 신호 크기 유지

  2. I2S_DEFAULT_CONFIG["fs"] 1000 고정 명시
     - 하이브리드 코드 fs=1000 과 일치 확인용 주석 추가

  3. get_stable_signal_for_main() 반환 신호에
     오프라인 안정화(stabilize) 추가 적용
     - normalize=True 로 정규화하여 하이브리드 분류기가
       올바르게 동작하도록 보정

  4. 소음 유형 이름 매핑 추가
     - sensor_input 내부 키("child_running")와
       하이브리드 코드 실험 이름("Child Running Noise") 연결

  5. 하이브리드 코드 섹션 11 교체용 코드 주석으로 제공
     - 두 파일을 같은 폴더에 놓고 하이브리드 코드 섹션 11을
       아래 주석 내용으로 교체하면 연동 완료
"""

import time
import numpy as np
from scipy.signal import butter, lfilter, medfilt


# =========================================================
# 레이턴시 목표값 상수
# =========================================================

TARGET_LATENCY_MS  = 0.3
TARGET_LATENCY_SEC = TARGET_LATENCY_MS / 1000.0


def max_buffer_samples(fs: int) -> int:
    """
    목표 레이턴시(0.3ms) 내에서 처리 가능한 최대 버퍼 샘플 수.

    fs=1000  → 0샘플 (샘플 단위 처리)
    fs=44100 → 13샘플
    fs=48000 → 14샘플
    """
    samples = int(fs * TARGET_LATENCY_SEC)
    return max(samples, 1)


# =========================================================
# 공통 DSP 유틸
# =========================================================

def _butter_filter(signal, cutoff, fs, btype="low", order=4):
    nyq = fs / 2
    normal_cutoff = np.clip(cutoff / nyq, 1e-4, 0.9999)
    b, a = butter(order, normal_cutoff, btype=btype)
    return lfilter(b, a, signal)


def rms(signal):
    """RMS 계산 - 하이브리드 코드와 동일."""
    return np.sqrt(np.mean(signal ** 2))


# =========================================================
# I2S 마이크 설정값
# =========================================================

I2S_DEFAULT_CONFIG = {
    # [변경 1] fs=1000 : 하이브리드 코드 fs=1000 과 일치
    "fs": 1000,

    "bit_depth": 24,

    # [변경 2] sensitivity_scale 0.05 → 1.0
    # 기존 0.05는 신호를 20분의 1로 축소 → 하이브리드 분류기 임계값 불일치
    # 1.0으로 설정해 하이브리드 코드의 generate_* 신호와 동일한 크기 유지
    "sensitivity_scale": 1.0,

    "noise_floor": 0.001,
    "dc_offset_threshold": 0.01,
    "clip_limit": 0.95,
    "target_latency_ms": TARGET_LATENCY_MS,
    "buffer_samples": max_buffer_samples(1000),
    "dc_filter_alpha": 0.995,
}

# =========================================================
# 진동 센서 설정값
# =========================================================

VIBRATION_SENSOR_CONFIG = {
    "freq_range":       (20, 200),
    "gain":             1.2,
    "median_kernel":    5,
    "highpass_cutoff":  5,
    "lowpass_cutoff":   200,
}

# =========================================================
# [변경 3] 소음 유형 이름 매핑
# sensor_input 내부 키 → 하이브리드 코드 실험 이름 / 권장 cutoff
# =========================================================

NOISE_META = {
    "child_running":   {"name": "Child Running Noise",        "cutoff": 150},
    "adult_footstep":  {"name": "Adult Heavy Footstep Noise", "cutoff": 120},
    "washing_machine": {"name": "Washing Machine Vibration",  "cutoff": 180},
    "chair_dragging":  {"name": "Chair Dragging Noise",       "cutoff": 200},
    "object_drop":     {"name": "Object Drop Impact Noise",   "cutoff": 120},
}

# 하위 호환용 (기존 코드에서 RECOMMENDED_CUTOFF 사용 시)
RECOMMENDED_CUTOFF = {k: v["cutoff"] for k, v in NOISE_META.items()}


# =========================================================
# SensorInput 클래스
# =========================================================

class SensorInput:
    """
    센서 & 입력 처리 클래스.

    핵심 목표:
    - 마이크에서 샘플을 받아 0.3ms 이내에 전처리 완료 후 반환.
    - 반환된 신호는 메인 제어 코드(hybrid_control)를 거쳐
      스피커로 역위상 출력되어 층간소음을 상쇄한다.
    """

    def __init__(
        self,
        fs           : int   = 1000,
        duration     : float = 8.0,
        i2s_config   : dict  = None,
        vibration_config: dict = None,
        random_seed  : int   = 10,
    ):
        self.fs       = fs
        self.duration = duration
        self.t        = np.arange(0, duration, 1 / fs)
        self.random_seed = random_seed

        self.i2s_config = i2s_config if i2s_config else I2S_DEFAULT_CONFIG.copy()
        self.i2s_config["buffer_samples"] = max_buffer_samples(fs)

        self.vibration_config = (
            vibration_config if vibration_config else VIBRATION_SENSOR_CONFIG.copy()
        )

        self._collected       : dict        = {}
        self._dc_filter_x_prev: float       = 0.0
        self._dc_filter_y_prev: float       = 0.0
        self._latency_log     : list[float] = []

        np.random.seed(self.random_seed)

    # --------------------------------------------------
    # 1. I2S 마이크 세팅
    # --------------------------------------------------

    def setup_i2s(self, fs: int = None, sensitivity_scale: float = None) -> dict:
        if fs is not None:
            self.fs = fs
            self.i2s_config["fs"] = fs
            self.i2s_config["buffer_samples"] = max_buffer_samples(fs)
            self.t = np.arange(0, self.duration, 1 / fs)

        if sensitivity_scale is not None:
            self.i2s_config["sensitivity_scale"] = sensitivity_scale

        buf            = self.i2s_config["buffer_samples"]
        actual_latency = buf / self.fs * 1000

        print("[I2S 마이크 세팅]")
        for k, v in self.i2s_config.items():
            print(f"  {k}: {v}")
        print(f"  → 버퍼 {buf}샘플 = 실제 레이턴시 {actual_latency:.4f}ms "
              f"(목표: {TARGET_LATENCY_MS}ms)")

        return self.i2s_config.copy()

    def read_sample_i2s(self, raw_sample: float) -> float:
        """
        I2S 마이크에서 1샘플을 읽어 전처리 후 반환.
        0.3ms 목표 핵심 함수 - 샘플 단위 처리로 버퍼 딜레이 없음.
        """
        scale = self.i2s_config["sensitivity_scale"]
        s     = raw_sample * scale

        alpha = self.i2s_config["dc_filter_alpha"]
        y = s - self._dc_filter_x_prev + alpha * self._dc_filter_y_prev
        self._dc_filter_x_prev = s
        self._dc_filter_y_prev = y

        clip_limit = self.i2s_config["clip_limit"]
        y = float(np.clip(y, -clip_limit, clip_limit))

        return y

    def read_i2s_mic(self, noise_type: str = "child_running") -> np.ndarray:
        """시뮬레이션 전체 신호를 샘플 단위로 순차 처리하여 반환."""
        raw_signal = self._simulate_noise(noise_type)
        self._reset_dc_filter()

        processed = np.zeros_like(raw_signal)
        for n, sample in enumerate(raw_signal):
            processed[n] = self.read_sample_i2s(sample)

        print(f"[I2S 마이크 읽기] noise_type={noise_type}, "
              f"RMS={rms(processed):.5f}, peak={np.max(np.abs(processed)):.5f}, "
              f"samples={len(processed)}")

        return processed

    # --------------------------------------------------
    # 2. 진동 센서 튜닝
    # --------------------------------------------------

    def tune_vibration_sensor(
        self,
        gain          : float = None,
        freq_range    : tuple = None,
        median_kernel : int   = None,
    ) -> dict:
        if gain is not None:
            self.vibration_config["gain"] = gain

        if freq_range is not None:
            if freq_range[0] >= freq_range[1]:
                raise ValueError("freq_range[0]은 freq_range[1]보다 작아야 합니다.")
            self.vibration_config["freq_range"]      = freq_range
            self.vibration_config["highpass_cutoff"] = freq_range[0]
            self.vibration_config["lowpass_cutoff"]  = freq_range[1]

        if median_kernel is not None:
            if median_kernel % 2 == 0:
                raise ValueError("median_kernel은 홀수여야 합니다.")
            self.vibration_config["median_kernel"] = median_kernel

        print("[진동 센서 튜닝]")
        for k, v in self.vibration_config.items():
            print(f"  {k}: {v}")

        return self.vibration_config.copy()

    def read_vibration_sensor(self, noise_type: str = "child_running") -> np.ndarray:
        raw    = self._simulate_noise(noise_type)
        gained = raw * self.vibration_config["gain"]

        hp = self.vibration_config["highpass_cutoff"]
        if hp > 0:
            gained = _butter_filter(gained, hp, self.fs, btype="high")

        lp = self.vibration_config["lowpass_cutoff"]
        if lp < self.fs / 2:
            gained = _butter_filter(gained, lp, self.fs, btype="low")

        kernel = self.vibration_config["median_kernel"]
        if kernel > 1:
            gained = medfilt(gained, kernel_size=kernel)

        print(f"[진동 센서 읽기] noise_type={noise_type}, "
              f"RMS={rms(gained):.5f}, peak={np.max(np.abs(gained)):.5f}")

        return gained

    # --------------------------------------------------
    # 3. 노이즈 데이터 수집
    # --------------------------------------------------

    def collect(
        self,
        noise_type : str,
        source     : str = "i2s",
        label      : str = None,
    ) -> np.ndarray:
        if source == "vibration":
            signal = self.read_vibration_sensor(noise_type)
        else:
            signal = self.read_i2s_mic(noise_type)

        key = label if label else noise_type
        self._collected[key] = signal

        print(f"[수집 완료] key='{key}', source={source}, samples={len(signal)}")

        return signal

    def collect_all(self, source: str = "i2s") -> dict:
        noise_types = [
            "child_running", "adult_footstep", "washing_machine",
            "chair_dragging", "object_drop",
        ]
        print("\n[전체 소음 데이터 수집 시작]")
        for nt in noise_types:
            self.collect(nt, source=source)
        print(f"[전체 수집 완료] 총 {len(self._collected)}개 유형\n")
        return self._collected.copy()

    def get_collected(self, label: str = None):
        if label is None:
            return self._collected.copy()
        if label not in self._collected:
            raise KeyError(f"'{label}' 키가 수집 데이터에 없습니다.")
        return self._collected[label]

    # --------------------------------------------------
    # 4. 입력 신호 안정화
    # --------------------------------------------------

    def stabilize_sample(self, sample: float) -> float:
        clip_limit = self.i2s_config["clip_limit"]
        return float(np.clip(sample, -clip_limit, clip_limit))

    def stabilize(
        self,
        signal          : np.ndarray,
        remove_dc       : bool = True,
        remove_outliers : bool = True,
        clip            : bool = True,
        normalize       : bool = True,
    ) -> np.ndarray:
        """
        수집된 전체 신호를 오프라인 안정화.

        [변경 3] normalize=True 기본값 유지
        하이브리드 코드의 classify_noise()가 신호 크기 기반으로
        소음 유형을 분류하므로 정규화 후 전달해야 정확한 분류 가능.
        """
        s = signal.copy()

        if remove_dc:
            dc        = np.mean(s)
            threshold = self.i2s_config["dc_offset_threshold"]
            if abs(dc) > threshold:
                s = s - dc
                print(f"[안정화] DC 오프셋 제거: {dc:.5f}")

        if remove_outliers:
            sigma        = np.std(s)
            mean         = np.mean(s)
            outlier_mask = (s > mean + 3 * sigma) | (s < mean - 3 * sigma)
            outlier_count = int(np.sum(outlier_mask))
            if outlier_count > 0:
                kernel     = self.vibration_config["median_kernel"]
                s_filtered = medfilt(s, kernel_size=kernel)
                s[outlier_mask] = s_filtered[outlier_mask]
                print(f"[안정화] 이상치 제거: {outlier_count}개 샘플")

        if clip:
            clip_limit    = self.i2s_config["clip_limit"]
            clipped_count = int(np.sum(np.abs(s) > clip_limit))
            if clipped_count > 0:
                s = np.clip(s, -clip_limit, clip_limit)
                print(f"[안정화] 클리핑 처리: {clipped_count}개 샘플")

        if normalize:
            peak = np.max(np.abs(s))
            if peak > 0:
                s = s / peak
                print(f"[안정화] 정규화 완료: peak={peak:.5f} → 1.0")

        print(f"[안정화 결과] RMS={rms(s):.5f}, peak={np.max(np.abs(s)):.5f}")

        return s

    def stabilize_all(self, signals: dict = None, **kwargs) -> dict:
        source = signals if signals is not None else self._collected
        if not source:
            raise ValueError("안정화할 신호가 없습니다.")
        stabilized = {}
        for label, sig in source.items():
            print(f"\n[안정화 시작] '{label}'")
            stabilized[label] = self.stabilize(sig, **kwargs)
        return stabilized

    # --------------------------------------------------
    # 5. 레이턴시 측정
    # --------------------------------------------------

    def measure_latency_sample(self, raw_sample: float) -> tuple:
        t_start   = time.perf_counter()
        processed = self.read_sample_i2s(raw_sample)
        t_end     = time.perf_counter()

        elapsed_ms = (t_end - t_start) * 1000
        self._latency_log.append(elapsed_ms)

        return processed, elapsed_ms

    def latency_report(self) -> dict:
        if not self._latency_log:
            print("[레이턴시 리포트] 측정 데이터 없음.")
            return {}

        log          = np.array(self._latency_log)
        within_target = float(np.mean(log <= TARGET_LATENCY_MS) * 100)

        report = {
            "samples_measured":  len(log),
            "avg_ms":            round(float(np.mean(log)), 4),
            "max_ms":            round(float(np.max(log)), 4),
            "min_ms":            round(float(np.min(log)), 4),
            "target_ms":         TARGET_LATENCY_MS,
            "within_target_pct": round(within_target, 1),
        }

        print("\n[레이턴시 리포트]")
        for k, v in report.items():
            print(f"  {k}: {v}")

        if within_target < 95:
            print(f"  ⚠ 경고: {100 - within_target:.1f}%의 샘플이 목표를 초과함")
        else:
            print(f"  ✓ 목표 달성: {within_target:.1f}%의 샘플이 {TARGET_LATENCY_MS}ms 이내")

        return report

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _reset_dc_filter(self):
        self._dc_filter_x_prev = 0.0
        self._dc_filter_y_prev = 0.0

    def _simulate_noise(self, noise_type: str) -> np.ndarray:
        t        = self.t
        fs       = self.fs
        duration = self.duration

        if noise_type == "child_running":
            return self._gen_child_running(t, fs, duration)
        elif noise_type == "adult_footstep":
            return self._gen_adult_footstep(t, fs, duration)
        elif noise_type == "washing_machine":
            return self._gen_washing_machine(t)
        elif noise_type == "chair_dragging":
            return self._gen_chair_dragging(t, fs)
        elif noise_type == "object_drop":
            return self._gen_object_drop(t, fs)
        else:
            raise ValueError(
                f"알 수 없는 noise_type: '{noise_type}'. "
                "child_running | adult_footstep | washing_machine | "
                "chair_dragging | object_drop 중 하나를 선택하세요."
            )

    def _gen_child_running(self, t, fs, duration):
        signal      = np.zeros_like(t)
        current_time = 0.4
        while current_time < duration - 0.5:
            interval     = np.random.uniform(0.25, 0.45)
            current_time += interval
            idx          = int(current_time * fs)
            strength     = np.random.uniform(0.8, 1.5)
            burst_len    = min(int(0.25 * fs), len(signal) - idx)
            if burst_len <= 0:
                continue
            burst_t   = np.arange(burst_len) / fs
            env       = np.exp(-18 * burst_t)
            burst     = strength * env * (
                np.sin(2 * np.pi * 30 * burst_t)
                + 0.8 * np.sin(2 * np.pi * 55 * burst_t)
                + 0.4 * np.sin(2 * np.pi * 90 * burst_t)
            )
            sharp_len = min(20, burst_len)
            sharp     = np.zeros(burst_len)
            sharp[:sharp_len] = strength * 1.8 * np.exp(-np.linspace(0, 4, sharp_len))
            signal[idx:idx + burst_len] += burst + sharp
        signal += (
            0.12 * np.sin(2 * np.pi * 25 * t)
            + 0.08 * np.sin(2 * np.pi * 45 * t)
            + 0.05 * np.random.randn(len(t))
        )
        return signal

    def _gen_adult_footstep(self, t, fs, duration):
        signal       = np.zeros_like(t)
        current_time = 0.6
        while current_time < duration - 0.5:
            interval     = np.random.uniform(0.55, 0.85)
            current_time += interval
            idx          = int(current_time * fs)
            strength     = np.random.uniform(1.3, 2.2)
            burst_len    = min(int(0.35 * fs), len(signal) - idx)
            if burst_len <= 0:
                continue
            burst_t   = np.arange(burst_len) / fs
            env       = np.exp(-10 * burst_t)
            burst     = strength * env * (
                np.sin(2 * np.pi * 20 * burst_t)
                + 0.9 * np.sin(2 * np.pi * 35 * burst_t)
                + 0.5 * np.sin(2 * np.pi * 60 * burst_t)
            )
            sharp_len = min(25, burst_len)
            sharp     = np.zeros(burst_len)
            sharp[:sharp_len] = strength * 2.2 * np.exp(-np.linspace(0, 5, sharp_len))
            signal[idx:idx + burst_len] += burst + sharp
        signal += (
            0.08 * np.sin(2 * np.pi * 30 * t)
            + 0.04 * np.random.randn(len(t))
        )
        return signal

    def _gen_washing_machine(self, t):
        signal = (
            0.8 * np.sin(2 * np.pi * 45 * t)
            + 0.5 * np.sin(2 * np.pi * 90 * t)
            + 0.25 * np.sin(2 * np.pi * 135 * t)
        )
        signal = signal * (1.0 + 0.2 * np.sin(2 * np.pi * 0.5 * t))
        signal += 0.04 * np.random.randn(len(t))
        return signal

    def _gen_chair_dragging(self, t, fs):
        signal = np.zeros_like(t)
        for start, end in [(1.0, 2.0), (3.0, 3.8), (5.2, 6.4)]:
            si, ei = int(start * fs), int(end * fs)
            length = ei - si
            if length <= 0:
                continue
            drag_t    = np.arange(length) / fs
            vibration = (
                0.5  * np.sin(2 * np.pi * 70  * drag_t)
                + 0.35 * np.sin(2 * np.pi * 110 * drag_t)
                + 0.2  * np.sin(2 * np.pi * 160 * drag_t)
            )
            roughness = np.clip(1.0 + 0.5 * np.random.randn(length), 0.2, 1.8)
            fade_len  = min(int(0.1 * fs), length // 2)
            envelope  = np.ones(length)
            if fade_len > 0:
                envelope[:fade_len]  = np.linspace(0, 1, fade_len)
                envelope[-fade_len:] = np.linspace(1, 0, fade_len)
            signal[si:ei] += vibration * roughness * envelope
        signal += (
            0.06 * np.sin(2 * np.pi * 40 * t)
            + 0.06 * np.random.randn(len(t))
        )
        return signal

    def _gen_object_drop(self, t, fs):
        signal = np.zeros_like(t)
        for drop_time in [1.2, 3.7, 6.1]:
            idx       = int(drop_time * fs)
            strength  = np.random.uniform(2.0, 3.2)
            burst_len = min(int(0.6 * fs), len(signal) - idx)
            if burst_len <= 0:
                continue
            burst_t   = np.arange(burst_len) / fs
            env       = np.exp(-7 * burst_t)
            burst     = strength * env * (
                np.sin(2 * np.pi * 18 * burst_t)
                + 0.9 * np.sin(2 * np.pi * 40 * burst_t)
                + 0.5 * np.sin(2 * np.pi * 75 * burst_t)
            )
            sharp_len = min(35, burst_len)
            sharp     = np.zeros(burst_len)
            sharp[:sharp_len] = strength * 2.8 * np.exp(-np.linspace(0, 6, sharp_len))
            signal[idx:idx + burst_len] += burst + sharp
        signal += 0.04 * np.random.randn(len(t))
        return signal


# =========================================================
# 메인 코드 연동 헬퍼 함수
# =========================================================

def realtime_anc_loop(
    noise_type   : str,
    sensor       : SensorInput,
    anc_callback = None,
) -> np.ndarray:
    """실시간 ANC 시뮬레이션 루프."""
    raw_signal = sensor._simulate_noise(noise_type)
    sensor._reset_dc_filter()

    output_signal = np.zeros_like(raw_signal)
    latencies     = []

    for n, raw_sample in enumerate(raw_signal):
        t_start          = time.perf_counter()
        processed_sample = sensor.read_sample_i2s(raw_sample)
        control_sample   = anc_callback(processed_sample) if anc_callback else -processed_sample
        output_signal[n] = control_sample
        latencies.append((time.perf_counter() - t_start) * 1000)

    latencies = np.array(latencies)
    within    = np.mean(latencies <= TARGET_LATENCY_MS) * 100

    print(f"\n[실시간 ANC 루프 완료] noise_type={noise_type}")
    print(f"  평균 레이턴시: {np.mean(latencies):.4f}ms  최대: {np.max(latencies):.4f}ms")
    print(f"  목표({TARGET_LATENCY_MS}ms) 달성률: {within:.1f}%")
    if within < 95:
        print(f"  ⚠ 경고: {100-within:.1f}%의 샘플이 목표를 초과함")
    else:
        print(f"  ✓ 목표 달성")

    return output_signal


def get_stable_signal_for_main(
    noise_type : str,
    fs         : int   = 1000,
    duration   : float = 8.0,
    source     : str   = "i2s",
) -> tuple:
    """
    하이브리드 코드 연동 진입점.

    [변경 4] 오프라인 안정화(stabilize) 추가
    - 수집 후 stabilize()를 통해 DC제거 + 이상치제거 + 정규화 적용
    - 하이브리드 코드 classify_noise()가 기대하는 신호 크기로 보정

    Returns
    -------
    tuple(np.ndarray, int)
        (안정화된 신호, 권장 cutoff Hz)

    사용 예 (하이브리드 코드 섹션 11 교체):
    ----------------------------------------
    from sensor_input import get_stable_signal_for_main

    child_signal, cutoff = get_stable_signal_for_main("child_running")
    experiments.append(run_experiment(
        name="Child Running Noise",
        input_signal=child_signal,
        event_info="from sensor_input",
        cutoff=cutoff,
        show_graph=SHOW_GRAPHS,
    ))
    """
    sensor = SensorInput(fs=fs, duration=duration)
    raw    = sensor.collect(noise_type, source=source)

    # [변경 4] 안정화 적용 → 하이브리드 분류기와 신호 크기 일치
    stable = sensor.stabilize(raw, remove_dc=True, remove_outliers=True,
                              clip=True, normalize=True)

    cutoff = NOISE_META.get(noise_type, {}).get("cutoff", 150)
    return stable, cutoff


# =========================================================
# 하이브리드 코드 섹션 11 교체용 코드 (주석)
# =========================================================
#
# 아래 코드를 하이브리드 코드 섹션 11 전체와 교체하면 연동 완료.
# sensor_input.py 와 하이브리드 코드가 같은 폴더에 있어야 함.
#
# ─────────────────────────────────────────────────────────
# from sensor_input import get_stable_signal_for_main
#
# experiments = []
#
# child_signal, cutoff = get_stable_signal_for_main("child_running")
# experiments.append(run_experiment(
#     name="Child Running Noise",
#     input_signal=child_signal,
#     event_info="from sensor_input",
#     cutoff=cutoff,
#     show_graph=SHOW_GRAPHS,
# ))
#
# adult_signal, cutoff = get_stable_signal_for_main("adult_footstep")
# experiments.append(run_experiment(
#     name="Adult Heavy Footstep Noise",
#     input_signal=adult_signal,
#     event_info="from sensor_input",
#     cutoff=cutoff,
#     show_graph=SHOW_GRAPHS,
# ))
#
# washing_signal, cutoff = get_stable_signal_for_main("washing_machine")
# experiments.append(run_experiment(
#     name="Washing Machine Vibration",
#     input_signal=washing_signal,
#     event_info="from sensor_input",
#     cutoff=cutoff,
#     show_graph=SHOW_GRAPHS,
# ))
#
# chair_signal, cutoff = get_stable_signal_for_main("chair_dragging")
# experiments.append(run_experiment(
#     name="Chair Dragging Noise",
#     input_signal=chair_signal,
#     event_info="from sensor_input",
#     cutoff=cutoff,
#     show_graph=SHOW_GRAPHS,
# ))
#
# drop_signal, cutoff = get_stable_signal_for_main("object_drop")
# experiments.append(run_experiment(
#     name="Object Drop Impact Noise",
#     input_signal=drop_signal,
#     event_info="from sensor_input",
#     cutoff=cutoff,
#     show_graph=SHOW_GRAPHS,
# ))
# ─────────────────────────────────────────────────────────


# =========================================================
# 단독 실행 테스트
# =========================================================

if __name__ == "__main__":
    print("=" * 60)
    print("sensor_input.py 단독 테스트")
    print(f"목표 레이턴시: {TARGET_LATENCY_MS}ms")
    print("=" * 60)

    sensor = SensorInput(fs=1000, duration=8.0)

    print("\n--- I2S 마이크 세팅 ---")
    sensor.setup_i2s()

    print("\n--- 진동 센서 튜닝 ---")
    sensor.tune_vibration_sensor(gain=1.5, freq_range=(20, 200), median_kernel=5)

    print("\n--- 실시간 ANC 루프 테스트 ---")
    output = realtime_anc_loop("child_running", sensor)
    print(f"  출력 신호 RMS: {rms(output):.5f}")

    print("\n--- 1샘플 레이턴시 측정 (100회) ---")
    for _ in range(100):
        sensor.measure_latency_sample(np.random.randn())
    sensor.latency_report()

    print("\n--- 전체 소음 수집 ---")
    collected = sensor.collect_all(source="i2s")

    print("\n--- 수집 결과 요약 ---")
    for label, sig in collected.items():
        print(f"  {label}: RMS={rms(sig):.5f}, peak={np.max(np.abs(sig)):.5f}, "
              f"samples={len(sig)}")

    print("\n--- 하이브리드 코드 연동 테스트 ---")
    signal, cutoff = get_stable_signal_for_main("washing_machine")
    print(f"  washing_machine → RMS={rms(signal):.5f}, cutoff={cutoff}Hz")
    print(f"  신호 길이: {len(signal)} (하이브리드 코드 기대값 8000과 일치: {len(signal)==8000})")

    print(f"\n[완료] 목표 레이턴시: {TARGET_LATENCY_MS}ms")