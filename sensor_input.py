"""
sensor_input.py

담당자: 이형규
역할: 센서 & 입력

=====================================
핵심 목표: 전체 파이프라인 레이턴시 0.3ms 이하
=====================================

[하드웨어 구성]
  아두이노:
    - 마이크 (아날로그 입력 A0) → 음성 신호 수집
    - BNO085 진동센서 (I2C) → 가속도/진동 수집
    - PCM5102 DAC + PAM8403 앰프 + 스피커 → 출력 (임도현 파트)
    - 캐패시터 → 전원 노이즈 제거 (회로 부품)

  라즈베리파이:
    - 아두이노와 시리얼(USB) 통신으로 데이터 수신
    - AI 분류 + hybrid_control 처리

[시리얼 통신 프로토콜]
  아두이노 → 라즈베리파이:
    "MIC:0.123,VIB:0.456,0.789,0.012\n"
    MIC: 마이크 값 (float)
    VIB: BNO085 x,y,z 가속도 (float 3개)

[변경 내역]
  1. I2S_DEFAULT_CONFIG → MIC_CONFIG 로 변경
     - 실제 아두이노 마이크 설정에 맞게 수정
     - 샘플레이트 1000Hz 유지 (시뮬레이션/실제 공통)

  2. VIBRATION_SENSOR_CONFIG → BNO085 스펙에 맞게 수정
     - BNO085: 가속도 범위 ±4g, I2C 통신
     - 층간소음 핵심 대역 20~200Hz 유지

  3. read_sample_i2s() → read_sample_mic() 로 이름 변경
     - 아두이노 아날로그 마이크 입력에 맞게 수정

  4. read_vibration_sensor() → BNO085 3축 데이터 처리로 수정
     - x,y,z 중 z축(수직) 중심으로 층간소음 감지

  5. ArduinoSerial 클래스 추가
     - 아두이노와 시리얼 통신으로 실시간 데이터 수신
     - 시뮬레이션 모드: pyserial 없으면 자동 전환

  6. get_stable_signal_for_main() 유지
     - 하이브리드 코드 연동 인터페이스 동일
"""

import time
import numpy as np
from scipy.signal import butter, lfilter, medfilt

# 시리얼 통신 (아두이노 연결용)
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("[경고] pyserial 미설치 → 시뮬레이션 모드로 실행")
    print("       설치: pip install pyserial")


# =========================================================
# 레이턴시 목표값 상수
# =========================================================

TARGET_LATENCY_MS  = 0.3
TARGET_LATENCY_SEC = TARGET_LATENCY_MS / 1000.0


def max_buffer_samples(fs: int) -> int:
    samples = int(fs * TARGET_LATENCY_SEC)
    return max(samples, 1)


# =========================================================
# 공통 DSP 유틸
# =========================================================

def _butter_filter(signal, cutoff, fs, btype="low", order=4):
    nyq           = fs / 2
    normal_cutoff = np.clip(cutoff / nyq, 1e-4, 0.9999)
    b, a          = butter(order, normal_cutoff, btype=btype)
    return lfilter(b, a, signal)


def rms(signal):
    return np.sqrt(np.mean(signal ** 2))


# =========================================================
# 1. 마이크 설정 (아두이노 아날로그 마이크)
# =========================================================

MIC_CONFIG = {
    # 시뮬레이션/실제 공통 샘플레이트
    # 하이브리드 코드 fs=1000 과 일치
    "fs": 1000,

    "bit_depth": 10,           # 아두이노 ADC 10bit (0~1023)
    "adc_max": 1023,           # ADC 최대값 (10bit)
    "adc_vref": 5.0,           # 아두이노 기준 전압 (V)
    "dc_bias": 512,            # 마이크 DC 바이어스 (무신호시 중간값)

    # sensitivity_scale=1.0: 하이브리드 분류기와 신호 크기 일치
    "sensitivity_scale": 1.0,

    "noise_floor":         0.001,
    "dc_offset_threshold": 0.01,
    "clip_limit":          0.95,
    "target_latency_ms":   TARGET_LATENCY_MS,
    "buffer_samples":      max_buffer_samples(1000),

    # DC 제거 IIR 필터 계수
    "dc_filter_alpha": 0.995,
}


# =========================================================
# 2. BNO085 진동센서 설정
# =========================================================

BNO085_CONFIG = {
    # BNO085 스펙
    # I2C 주소: 0x4A (기본) 또는 0x4B
    "i2c_address":  0x4A,

    # 가속도 범위: ±4g (층간소음 감지에 적합)
    # ±2g: 민감, ±4g: 일반, ±8g: 강한 충격
    "accel_range_g": 4,

    # 층간소음 핵심 주파수 대역 (Hz)
    "freq_range":      (20, 200),
    "highpass_cutoff": 20,
    "lowpass_cutoff":  200,

    # 감도 배율
    # BNO085 가속도 출력(m/s²) → 정규화 스케일
    # 1g = 9.81 m/s², ±4g 범위 → 최대 39.24 m/s²
    "gain": 1.0 / 39.24,

    # 이상치 제거 메디안 필터 크기 (홀수)
    "median_kernel": 5,

    # 사용 축: z축 (수직 방향) - 층간소음 감지에 최적
    # 'x': 좌우, 'y': 앞뒤, 'z': 상하(층간소음)
    "axis": "z",

    # 샘플레이트: 하이브리드 코드와 일치
    "sample_rate": 1000,
}


# =========================================================
# 소음 유형 메타데이터
# =========================================================

NOISE_META = {
    "child_running":   {"name": "Child Running Noise",        "cutoff": 150},
    "adult_footstep":  {"name": "Adult Heavy Footstep Noise", "cutoff": 120},
    "washing_machine": {"name": "Washing Machine Vibration",  "cutoff": 180},
    "chair_dragging":  {"name": "Chair Dragging Noise",       "cutoff": 200},
    "object_drop":     {"name": "Object Drop Impact Noise",   "cutoff": 120},
}

RECOMMENDED_CUTOFF = {k: v["cutoff"] for k, v in NOISE_META.items()}


# =========================================================
# 3. 아두이노 시리얼 통신 클래스 (신규 추가)
# =========================================================

class ArduinoSerial:
    """
    아두이노와 시리얼(USB) 통신으로 센서 데이터를 수신하는 클래스.

    아두이노 송신 포맷:
        "MIC:0.123,VIB:0.456,0.789,0.012\n"
        MIC: 마이크 정규화 값 (-1.0 ~ 1.0)
        VIB: BNO085 x, y, z 가속도 (m/s² 정규화)

    아두이노 코드 예시 (Arduino IDE):
    ------------------------------------
    #include <Wire.h>
    // BNO085 라이브러리 필요: Adafruit BNO08x

    void setup() {
        Serial.begin(115200);
        // BNO085 초기화
    }

    void loop() {
        int mic_raw = analogRead(A0);
        float mic = (mic_raw - 512) / 512.0;  // -1~1 정규화

        // BNO085에서 가속도 읽기
        float ax, ay, az;
        // bno085.getAccelerometer(ax, ay, az);

        Serial.print("MIC:");
        Serial.print(mic, 4);
        Serial.print(",VIB:");
        Serial.print(ax, 4);
        Serial.print(",");
        Serial.print(ay, 4);
        Serial.print(",");
        Serial.println(az, 4);

        delayMicroseconds(1000);  // 1ms = 1000Hz 샘플레이트
    }
    ------------------------------------
    """

    def __init__(
        self,
        port      : str = None,
        baudrate  : int = 115200,
        timeout   : float = 1.0,
    ):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self.ser      = None
        self._connected = False

        if SERIAL_AVAILABLE:
            self._connect()
        else:
            print("[아두이노] pyserial 없음 → 시뮬레이션 모드")

    def _connect(self):
        """시리얼 포트 자동 탐색 및 연결."""
        # 포트 지정 없으면 자동 탐색
        if self.port is None:
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                # 아두이노 관련 포트 자동 감지
                if "Arduino" in p.description or "ttyUSB" in p.device or "ttyACM" in p.device:
                    self.port = p.device
                    print(f"[아두이노] 포트 자동 감지: {self.port}")
                    break

        if self.port is None:
            print("[아두이노] 포트 감지 실패 → 시뮬레이션 모드")
            return

        try:
            self.ser = serial.Serial(
                port     = self.port,
                baudrate = self.baudrate,
                timeout  = self.timeout,
            )
            time.sleep(2)  # 아두이노 리셋 대기
            self._connected = True
            print(f"[아두이노] 연결 성공: {self.port} ({self.baudrate}bps)")
        except Exception as e:
            print(f"[아두이노] 연결 실패: {e} → 시뮬레이션 모드")

    def read_one(self) -> dict:
        """
        아두이노에서 1줄 읽어 파싱.

        Returns
        -------
        dict
            {"mic": float, "vib_x": float, "vib_y": float, "vib_z": float}
            연결 실패 시 None 반환
        """
        if not self._connected or self.ser is None:
            return None

        try:
            line = self.ser.readline().decode("utf-8").strip()
            # 포맷: "MIC:0.123,VIB:0.456,0.789,0.012"
            parts   = line.split(",")
            mic_val = float(parts[0].split(":")[1])
            vib_x   = float(parts[1].split(":")[1])
            vib_y   = float(parts[2])
            vib_z   = float(parts[3])

            return {
                "mic":   mic_val,
                "vib_x": vib_x,
                "vib_y": vib_y,
                "vib_z": vib_z,
            }
        except Exception:
            return None

    def read_samples(self, n_samples: int) -> tuple:
        """
        n_samples 개만큼 샘플을 수집하여 배열로 반환.

        Returns
        -------
        tuple(np.ndarray, np.ndarray)
            (mic_array, vib_z_array) shape=(n_samples,)
        """
        mic_buf = np.zeros(n_samples)
        vib_buf = np.zeros(n_samples)
        count   = 0

        print(f"[아두이노] {n_samples}샘플 수집 중...")

        while count < n_samples:
            data = self.read_one()
            if data is not None:
                mic_buf[count] = data["mic"]
                # BNO085 config 기준 axis=z 사용
                vib_buf[count] = data["vib_z"]
                count += 1

        print(f"[아두이노] 수집 완료: {count}샘플")
        return mic_buf, vib_buf

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("[아두이노] 시리얼 포트 닫힘")


# =========================================================
# SensorInput 클래스
# =========================================================

class SensorInput:
    """
    센서 & 입력 처리 클래스.

    실제 하드웨어 흐름:
      아두이노 (마이크 + BNO085)
        → 시리얼 통신
        → 라즈베리파이 SensorInput
        → 전처리 (0.3ms 이내)
        → hybrid_control (AI 분류 + DSP)
    """

    def __init__(
        self,
        fs               : int   = 1000,
        duration         : float = 8.0,
        mic_config       : dict  = None,
        bno085_config    : dict  = None,
        serial_port      : str   = None,
        random_seed      : int   = 10,
    ):
        self.fs       = fs
        self.duration = duration
        self.t        = np.arange(0, duration, 1 / fs)
        self.random_seed = random_seed

        # 마이크 설정
        self.mic_config = mic_config if mic_config else MIC_CONFIG.copy()
        self.mic_config["buffer_samples"] = max_buffer_samples(fs)

        # BNO085 진동센서 설정
        self.bno085_config = bno085_config if bno085_config else BNO085_CONFIG.copy()

        # 아두이노 시리얼 연결
        self.arduino = ArduinoSerial(port=serial_port)

        self._collected        : dict        = {}
        self._dc_filter_x_prev : float       = 0.0
        self._dc_filter_y_prev : float       = 0.0
        self._latency_log      : list[float] = []

        np.random.seed(self.random_seed)

    # --------------------------------------------------
    # 1. I2S 마이크 세팅
    # --------------------------------------------------

    def setup_i2s(
        self,
        fs                : int   = None,
        sensitivity_scale : float = None,
    ) -> dict:
        """
        마이크 파라미터 설정.
        (아두이노 아날로그 마이크 기준으로 수정)
        """
        if fs is not None:
            self.fs = fs
            self.mic_config["fs"]             = fs
            self.mic_config["buffer_samples"] = max_buffer_samples(fs)
            self.t = np.arange(0, self.duration, 1 / fs)

        if sensitivity_scale is not None:
            self.mic_config["sensitivity_scale"] = sensitivity_scale

        buf            = self.mic_config["buffer_samples"]
        actual_latency = buf / self.fs * 1000

        print("[마이크 세팅 - 아두이노 아날로그 마이크]")
        for k, v in self.mic_config.items():
            print(f"  {k}: {v}")
        print(f"  → 버퍼 {buf}샘플 = 레이턴시 {actual_latency:.4f}ms "
              f"(목표: {TARGET_LATENCY_MS}ms)")

        return self.mic_config.copy()

    def read_sample_mic(self, raw_sample: float) -> float:
        """
        마이크 1샘플 전처리 (0.3ms 목표 핵심 함수).

        아두이노에서 -1~1 정규화된 값을 받아:
        1) sensitivity_scale 적용
        2) DC 제거 IIR 필터
        3) 클리핑
        """
        scale = self.mic_config["sensitivity_scale"]
        s     = raw_sample * scale

        # DC 제거 IIR (샘플 단위, 레이턴시 0)
        alpha = self.mic_config["dc_filter_alpha"]
        y     = s - self._dc_filter_x_prev + alpha * self._dc_filter_y_prev
        self._dc_filter_x_prev = s
        self._dc_filter_y_prev = y

        clip_limit = self.mic_config["clip_limit"]
        return float(np.clip(y, -clip_limit, clip_limit))

    # 하위 호환용 별칭
    def read_sample_i2s(self, raw_sample: float) -> float:
        return self.read_sample_mic(raw_sample)

    def read_i2s_mic(self, noise_type: str = "child_running") -> np.ndarray:
        """
        실제: 아두이노 시리얼에서 마이크 데이터 수신
        시뮬레이션: 내부 신호 생성
        """
        n_samples = int(self.duration * self.fs)

        # 실제 아두이노 연결 시
        if self.arduino._connected:
            mic_buf, _ = self.arduino.read_samples(n_samples)
            raw_signal = mic_buf
            print(f"[마이크] 아두이노 실측 데이터 수신 완료")
        else:
            # 시뮬레이션 모드
            raw_signal = self._simulate_noise(noise_type)

        self._reset_dc_filter()
        processed = np.zeros_like(raw_signal)
        for n, sample in enumerate(raw_signal):
            processed[n] = self.read_sample_mic(sample)

        print(f"[마이크 읽기] noise_type={noise_type}, "
              f"RMS={rms(processed):.5f}, "
              f"peak={np.max(np.abs(processed)):.5f}, "
              f"samples={len(processed)}")

        return processed

    # --------------------------------------------------
    # 2. 진동 센서 튜닝 (BNO085)
    # --------------------------------------------------

    def tune_vibration_sensor(
        self,
        gain          : float = None,
        freq_range    : tuple = None,
        median_kernel : int   = None,
        axis          : str   = None,
    ) -> dict:
        """
        BNO085 진동센서 감도 및 대역 튜닝.

        axis: 'x'(좌우) / 'y'(앞뒤) / 'z'(상하, 층간소음 권장)
        """
        if gain is not None:
            self.bno085_config["gain"] = gain

        if freq_range is not None:
            if freq_range[0] >= freq_range[1]:
                raise ValueError("freq_range[0] < freq_range[1] 이어야 합니다.")
            self.bno085_config["freq_range"]      = freq_range
            self.bno085_config["highpass_cutoff"] = freq_range[0]
            self.bno085_config["lowpass_cutoff"]  = freq_range[1]

        if median_kernel is not None:
            if median_kernel % 2 == 0:
                raise ValueError("median_kernel은 홀수여야 합니다.")
            self.bno085_config["median_kernel"] = median_kernel

        if axis is not None:
            if axis not in ("x", "y", "z"):
                raise ValueError("axis는 'x', 'y', 'z' 중 하나여야 합니다.")
            self.bno085_config["axis"] = axis

        print("[BNO085 진동센서 튜닝]")
        for k, v in self.bno085_config.items():
            print(f"  {k}: {v}")

        return self.bno085_config.copy()

    def read_vibration_sensor(self, noise_type: str = "child_running") -> np.ndarray:
        """
        BNO085 진동 데이터 수신 및 필터링.

        실제: 아두이노 시리얼에서 z축 가속도 수신
        시뮬레이션: 내부 신호 생성
        """
        n_samples = int(self.duration * self.fs)

        # 실제 아두이노 연결 시
        if self.arduino._connected:
            _, vib_buf = self.arduino.read_samples(n_samples)
            raw = vib_buf * self.bno085_config["gain"]
            print(f"[BNO085] 아두이노 실측 데이터 수신 완료")
        else:
            # 시뮬레이션 모드
            raw   = self._simulate_noise(noise_type)
            raw   = raw * self.bno085_config["gain"]

        # 하이패스 (DC 및 초저주파 제거)
        hp = self.bno085_config["highpass_cutoff"]
        if hp > 0:
            raw = _butter_filter(raw, hp, self.fs, btype="high")

        # 로우패스 (층간소음 관심 대역만 통과)
        lp = self.bno085_config["lowpass_cutoff"]
        if lp < self.fs / 2:
            raw = _butter_filter(raw, lp, self.fs, btype="low")

        # 메디안 필터 (이상치 제거)
        kernel = self.bno085_config["median_kernel"]
        if kernel > 1:
            raw = medfilt(raw, kernel_size=kernel)

        print(f"[BNO085 진동센서] noise_type={noise_type}, "
              f"RMS={rms(raw):.5f}, peak={np.max(np.abs(raw)):.5f}")

        return raw

    # --------------------------------------------------
    # 3. 노이즈 데이터 수집
    # --------------------------------------------------

    def collect(
        self,
        noise_type : str,
        source     : str = "mic",
        label      : str = None,
    ) -> np.ndarray:
        """
        소음 데이터 수집.

        source: "mic" (마이크) 또는 "vibration" (BNO085)
        """
        if source == "vibration":
            signal = self.read_vibration_sensor(noise_type)
        else:
            signal = self.read_i2s_mic(noise_type)

        key                  = label if label else noise_type
        self._collected[key] = signal

        print(f"[수집 완료] key='{key}', source={source}, samples={len(signal)}")
        return signal

    def collect_all(self, source: str = "mic") -> dict:
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
            raise KeyError(f"'{label}' 키가 없습니다.")
        return self._collected[label]

    # --------------------------------------------------
    # 4. 입력 신호 안정화
    # --------------------------------------------------

    def stabilize_sample(self, sample: float) -> float:
        clip_limit = self.mic_config["clip_limit"]
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
        전체 신호 오프라인 안정화.

        순서: DC제거 → 이상치제거 → 클리핑 → 정규화
        normalize=True: 하이브리드 classify_noise() 와 신호 크기 일치
        """
        s = signal.copy()

        if remove_dc:
            dc        = np.mean(s)
            threshold = self.mic_config["dc_offset_threshold"]
            if abs(dc) > threshold:
                s = s - dc
                print(f"[안정화] DC 오프셋 제거: {dc:.5f}")

        if remove_outliers:
            sigma        = np.std(s)
            mean         = np.mean(s)
            outlier_mask = (s > mean + 3 * sigma) | (s < mean - 3 * sigma)
            outlier_count = int(np.sum(outlier_mask))
            if outlier_count > 0:
                kernel     = self.bno085_config["median_kernel"]
                s_filtered = medfilt(s, kernel_size=kernel)
                s[outlier_mask] = s_filtered[outlier_mask]
                print(f"[안정화] 이상치 제거: {outlier_count}개 샘플")

        if clip:
            clip_limit    = self.mic_config["clip_limit"]
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
    # 레이턴시 측정
    # --------------------------------------------------

    def measure_latency_sample(self, raw_sample: float) -> tuple:
        t_start    = time.perf_counter()
        processed  = self.read_sample_mic(raw_sample)
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        self._latency_log.append(elapsed_ms)
        return processed, elapsed_ms

    def latency_report(self) -> dict:
        if not self._latency_log:
            print("[레이턴시 리포트] 측정 데이터 없음.")
            return {}

        log           = np.array(self._latency_log)
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
            print(f"  ⚠ 경고: {100-within_target:.1f}%의 샘플이 목표를 초과함")
        else:
            print(f"  ✓ 목표 달성: {within_target:.1f}%가 {TARGET_LATENCY_MS}ms 이내")

        return report

    def close(self):
        """리소스 정리."""
        self.arduino.close()

    # --------------------------------------------------
    # 내부 헬퍼 (시뮬레이션용)
    # --------------------------------------------------

    def _reset_dc_filter(self):
        self._dc_filter_x_prev = 0.0
        self._dc_filter_y_prev = 0.0

    def _simulate_noise(self, noise_type: str) -> np.ndarray:
        t = self.t; fs = self.fs; duration = self.duration
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
            raise ValueError(f"알 수 없는 noise_type: '{noise_type}'")

    def _gen_child_running(self, t, fs, duration):
        signal = np.zeros_like(t)
        current_time = 0.4
        while current_time < duration - 0.5:
            current_time += np.random.uniform(0.25, 0.45)
            idx = int(current_time * fs)
            strength  = np.random.uniform(0.8, 1.5)
            burst_len = min(int(0.25 * fs), len(signal) - idx)
            if burst_len <= 0: continue
            burst_t = np.arange(burst_len) / fs
            env     = np.exp(-18 * burst_t)
            burst   = strength * env * (
                np.sin(2*np.pi*30*burst_t) + 0.8*np.sin(2*np.pi*55*burst_t)
                + 0.4*np.sin(2*np.pi*90*burst_t))
            sharp = np.zeros(burst_len)
            sl = min(20, burst_len)
            sharp[:sl] = strength * 1.8 * np.exp(-np.linspace(0, 4, sl))
            signal[idx:idx+burst_len] += burst + sharp
        signal += (0.12*np.sin(2*np.pi*25*t) + 0.08*np.sin(2*np.pi*45*t)
                   + 0.05*np.random.randn(len(t)))
        return signal

    def _gen_adult_footstep(self, t, fs, duration):
        signal = np.zeros_like(t)
        current_time = 0.6
        while current_time < duration - 0.5:
            current_time += np.random.uniform(0.55, 0.85)
            idx = int(current_time * fs)
            strength  = np.random.uniform(1.3, 2.2)
            burst_len = min(int(0.35 * fs), len(signal) - idx)
            if burst_len <= 0: continue
            burst_t = np.arange(burst_len) / fs
            env     = np.exp(-10 * burst_t)
            burst   = strength * env * (
                np.sin(2*np.pi*20*burst_t) + 0.9*np.sin(2*np.pi*35*burst_t)
                + 0.5*np.sin(2*np.pi*60*burst_t))
            sharp = np.zeros(burst_len)
            sl = min(25, burst_len)
            sharp[:sl] = strength * 2.2 * np.exp(-np.linspace(0, 5, sl))
            signal[idx:idx+burst_len] += burst + sharp
        signal += 0.08*np.sin(2*np.pi*30*t) + 0.04*np.random.randn(len(t))
        return signal

    def _gen_washing_machine(self, t):
        s  = (0.8*np.sin(2*np.pi*45*t) + 0.5*np.sin(2*np.pi*90*t)
              + 0.25*np.sin(2*np.pi*135*t))
        s *= (1.0 + 0.2*np.sin(2*np.pi*0.5*t))
        s += 0.04*np.random.randn(len(t))
        return s

    def _gen_chair_dragging(self, t, fs):
        signal = np.zeros_like(t)
        for start, end in [(1.0,2.0),(3.0,3.8),(5.2,6.4)]:
            si, ei = int(start*fs), int(end*fs)
            length = ei - si
            if length <= 0: continue
            drag_t = np.arange(length) / fs
            vib    = (0.5*np.sin(2*np.pi*70*drag_t)
                      + 0.35*np.sin(2*np.pi*110*drag_t)
                      + 0.2*np.sin(2*np.pi*160*drag_t))
            roughness = np.clip(1.0 + 0.5*np.random.randn(length), 0.2, 1.8)
            fade_len  = min(int(0.1*fs), length//2)
            env = np.ones(length)
            if fade_len > 0:
                env[:fade_len]  = np.linspace(0, 1, fade_len)
                env[-fade_len:] = np.linspace(1, 0, fade_len)
            signal[si:ei] += vib * roughness * env
        signal += 0.06*np.sin(2*np.pi*40*t) + 0.06*np.random.randn(len(t))
        return signal

    def _gen_object_drop(self, t, fs):
        signal = np.zeros_like(t)
        for drop_time in [1.2, 3.7, 6.1]:
            idx      = int(drop_time * fs)
            strength = np.random.uniform(2.0, 3.2)
            burst_len = min(int(0.6*fs), len(signal)-idx)
            if burst_len <= 0: continue
            burst_t = np.arange(burst_len) / fs
            env     = np.exp(-7 * burst_t)
            burst   = strength * env * (
                np.sin(2*np.pi*18*burst_t) + 0.9*np.sin(2*np.pi*40*burst_t)
                + 0.5*np.sin(2*np.pi*75*burst_t))
            sharp = np.zeros(burst_len)
            sl = min(35, burst_len)
            sharp[:sl] = strength * 2.8 * np.exp(-np.linspace(0, 6, sl))
            signal[idx:idx+burst_len] += burst + sharp
        signal += 0.04*np.random.randn(len(t))
        return signal


# =========================================================
# 하이브리드 코드 연동 함수
# =========================================================

def realtime_anc_loop(
    noise_type   : str,
    sensor       : SensorInput,
    anc_callback = None,
) -> np.ndarray:
    """실시간 ANC 루프 - 아두이노 연결 시 실측, 없으면 시뮬레이션."""
    n_samples  = int(sensor.duration * sensor.fs)
    raw_signal = sensor._simulate_noise(noise_type)
    sensor._reset_dc_filter()

    output_signal = np.zeros_like(raw_signal)
    latencies     = []

    for n, raw_sample in enumerate(raw_signal):
        t_start          = time.perf_counter()
        processed_sample = sensor.read_sample_mic(raw_sample)
        control_sample   = (anc_callback(processed_sample)
                            if anc_callback else -processed_sample)
        output_signal[n] = control_sample
        latencies.append((time.perf_counter() - t_start) * 1000)

    latencies = np.array(latencies)
    within    = np.mean(latencies <= TARGET_LATENCY_MS) * 100

    print(f"\n[ANC 루프 완료] noise_type={noise_type}")
    print(f"  평균 레이턴시: {np.mean(latencies):.4f}ms  최대: {np.max(latencies):.4f}ms")
    print(f"  목표({TARGET_LATENCY_MS}ms) 달성률: {within:.1f}%")
    print(f"  {'✓ 목표 달성' if within >= 95 else '⚠ 목표 미달'}")

    return output_signal


def get_stable_signal_for_main(
    noise_type  : str,
    fs          : int   = 1000,
    duration    : float = 8.0,
    source      : str   = "mic",
    serial_port : str   = None,
) -> tuple:
    """
    하이브리드 코드 연동 진입점.

    실제 아두이노 연결 시: serial_port 지정 (예: "/dev/ttyUSB0")
    시뮬레이션 시: serial_port=None

    Returns: (안정화된 신호, 권장 cutoff Hz)
    """
    sensor = SensorInput(fs=fs, duration=duration, serial_port=serial_port)
    raw    = sensor.collect(noise_type, source=source)
    stable = sensor.stabilize(raw, remove_dc=True, remove_outliers=True,
                              clip=True, normalize=True)
    cutoff = NOISE_META.get(noise_type, {}).get("cutoff", 150)
    sensor.close()
    return stable, cutoff