"""
=============================================================
sensor_input.py
이형규 - 센서 & 입력 모듈 (라즈베리파이 전용)
=============================================================

담당 기능:
  1) I2S 마이크 세팅       → I2SMicConfig / capture_i2s_mic()
  2) 진동 센서 튜닝         → MPU6050Config / capture_mpu6050()
  3) 노이즈 데이터 수집     → SensorDataCollector
  4) 입력 신호 안정화       → InputSignalStabilizer / run_sensor_pipeline()

연동 방법 (메인 코드에서):
    from sensor_input import run_sensor_pipeline
    input_signal = run_sensor_pipeline(raw_signal=<시뮬레이션 신호>)

하드웨어 구성 (라즈베리파이):
  [I2S 마이크]  BCK=GPIO18 / LRCLK=GPIO19 / DATA=GPIO20
  [MPU-6050]    SDA=GPIO2  / SCL=GPIO3   (I2C 버스 1)

설치 패키지:
  pip install sounddevice smbus2 numpy scipy
  sudo apt install fonts-nanum
=============================================================
"""

import time
import numpy as np
from scipy.signal import butter, lfilter

# ── 라즈베리파이 하드웨어 라이브러리 ──────────────────────────
# 없으면 자동으로 시뮬레이션 모드 전환
try:
    import sounddevice as sd
    _SD = True
except ImportError:
    _SD = False
    print("[sensor_input] sounddevice 없음 → I2S 마이크 시뮬레이션 모드")

try:
    import smbus2
    _SMBUS = True
except ImportError:
    _SMBUS = False
    print("[sensor_input] smbus2 없음 → MPU-6050 시뮬레이션 모드")


# =========================================================
# 공통 상수
# =========================================================
MIC_FS      = 44100   # I2S 마이크 캡처 주파수 (Hz)
DSP_FS      = 1000    # 메인 코드 DSP 처리 주파수 (Hz)
DOWNSAMPLE  = MIC_FS // DSP_FS   # 다운샘플 비율 (44)

MPU6050_ADDR     = 0x68
MPU6050_PWR_REG  = 0x6B
MPU6050_ACCEL_REG= 0x3B
I2C_BUS          = 1


# =========================================================
# [1] I2S 마이크 세팅
# =========================================================

class I2SMicConfig:
    """
    I2S 마이크 설정.

    지원 마이크 : INMP441, SPH0645, ICS-43434
    GPIO 핀     : BCK=18 / LRCLK=19 / DATA=20
    /boot/config.txt 에 'dtparam=i2s=on' 또는
                        'dtoverlay=googlevoicehat-soundcard' 필요.

    Parameters
    ----------
    sample_rate : 캡처 주파수 (Hz) — 기본 44100
    bit_depth   : 비트 깊이 (16/24/32)
    channels    : 채널 수 (1=모노)
    device_name : sounddevice 장치명 ('arecord -l' 로 확인)
    gain_db     : 소프트웨어 게인 (dB)
    hp_cutoff   : 직류 차단 하이패스 (Hz)
    """

    def __init__(
        self,
        sample_rate : int   = MIC_FS,
        bit_depth   : int   = 32,
        channels    : int   = 1,
        device_name : str   = "default",
        gain_db     : float = 6.0,
        hp_cutoff   : float = 20.0,
    ):
        self.sample_rate  = sample_rate
        self.bit_depth    = bit_depth
        self.channels     = channels
        self.device_name  = device_name
        self.gain_db      = gain_db
        self.hp_cutoff    = hp_cutoff
        self.gain_linear  = 10 ** (gain_db / 20.0)

    def __repr__(self):
        return (
            f"I2SMicConfig(fs={self.sample_rate}Hz, {self.bit_depth}bit, "
            f"gain={self.gain_db}dB, HP={self.hp_cutoff}Hz, "
            f"device='{self.device_name}')"
        )


def capture_i2s_mic(mic_cfg: I2SMicConfig, duration: float) -> np.ndarray:
    """
    I2S 마이크에서 오디오를 캡처한다.

    sounddevice 없거나 하드웨어 오류 → 가우시안 잡음으로 대체.

    Returns
    -------
    signal : float64, 정규화 [-1, 1], shape=(duration*sample_rate,)
    """
    n = int(duration * mic_cfg.sample_rate)

    if _SD:
        try:
            print(f"  [I2S] 녹음 시작 ({duration}s, {mic_cfg.sample_rate}Hz) ...")
            raw = sd.rec(
                frames     = n,
                samplerate = mic_cfg.sample_rate,
                channels   = mic_cfg.channels,
                dtype      = "float32",
                device     = mic_cfg.device_name,
                blocking   = True,
            )
            sig = raw[:, 0].astype(np.float64)
            print("  [I2S] 녹음 완료")
        except Exception as e:
            print(f"  [I2S] 오류({e}) → 시뮬레이션 신호 사용")
            sig = np.random.randn(n) * 0.05
    else:
        sig = np.random.randn(n) * 0.05

    # 소프트웨어 게인
    sig = sig * mic_cfg.gain_linear

    # 하이패스 (직류 제거)
    nyq = mic_cfg.sample_rate / 2.0
    b, a = butter(2, mic_cfg.hp_cutoff / nyq, btype="high")
    sig = lfilter(b, a, sig)

    # 정규화
    pk = np.max(np.abs(sig))
    if pk > 1e-9:
        sig = sig / pk
    return sig


# =========================================================
# [2] 진동 센서 튜닝 (MPU-6050)
# =========================================================

class MPU6050Config:
    """
    MPU-6050 진동 센서(가속도계) 설정.

    I2C 핀  : SDA=GPIO2(Pin3) / SCL=GPIO3(Pin5)
    전원    : 3.3V(Pin1) / GND(Pin6)
    주소    : AD0=GND → 0x68 / AD0=3.3V → 0x69
    확인    : i2cdetect -y 1

    Parameters
    ----------
    i2c_bus      : I2C 버스 번호 (라즈베리파이 기본 1)
    address      : MPU-6050 I2C 주소
    accel_range  : 가속도 측정 범위 (RANGE_2G ~ RANGE_16G)
    sample_rate  : 읽기 루프 주파수 (Hz)
    axis         : 측정 축 ('x'/'y'/'z') — 층간소음은 'z' 권장
    lp_cutoff    : 저역통과 차단 주파수 (Hz)
    """

    RANGE_2G  = 0x00   # ±2g,  LSB=16384
    RANGE_4G  = 0x08   # ±4g,  LSB=8192
    RANGE_8G  = 0x10   # ±8g,  LSB=4096
    RANGE_16G = 0x18   # ±16g, LSB=2048

    _LSB = {0x00: 16384.0, 0x08: 8192.0, 0x10: 4096.0, 0x18: 2048.0}

    def __init__(
        self,
        i2c_bus     : int   = I2C_BUS,
        address     : int   = MPU6050_ADDR,
        accel_range : int   = None,
        sample_rate : int   = DSP_FS,
        axis        : str   = "z",
        lp_cutoff   : float = 200.0,
    ):
        self.i2c_bus     = i2c_bus
        self.address     = address
        self.accel_range = accel_range if accel_range is not None else self.RANGE_2G
        self.sample_rate = sample_rate
        self.axis        = axis.lower()
        self.lp_cutoff   = lp_cutoff
        self.lsb         = self._LSB.get(self.accel_range, 16384.0)

    def __repr__(self):
        rng = {0x00:"±2g",0x08:"±4g",0x10:"±8g",0x18:"±16g"}
        return (
            f"MPU6050Config(bus={self.i2c_bus}, addr=0x{self.address:02X}, "
            f"range={rng.get(self.accel_range,'?')}, "
            f"axis={self.axis}, LP={self.lp_cutoff}Hz)"
        )


def _mpu_init(bus, cfg: MPU6050Config):
    """MPU-6050 슬립 해제 + 가속도 범위 설정."""
    bus.write_byte_data(cfg.address, MPU6050_PWR_REG, 0x00)
    time.sleep(0.05)
    bus.write_byte_data(cfg.address, 0x1C, cfg.accel_range)


def _read_accel(bus, cfg: MPU6050Config) -> float:
    """지정 축 가속도 1샘플 읽기 (g 단위)."""
    offset = {"x": 0, "y": 2, "z": 4}.get(cfg.axis, 4)
    d = bus.read_i2c_block_data(cfg.address, MPU6050_ACCEL_REG + offset, 2)
    raw = (d[0] << 8) | d[1]
    if raw > 32767:
        raw -= 65536
    return raw / cfg.lsb


def capture_mpu6050(mpu_cfg: MPU6050Config, duration: float) -> np.ndarray:
    """
    MPU-6050에서 가속도를 수집한다.

    smbus2 없거나 하드웨어 오류 → 가우시안 잡음으로 대체.

    Returns
    -------
    signal : float64, 정규화 [-1, 1], shape=(duration*sample_rate,)
    """
    n        = int(duration * mpu_cfg.sample_rate)
    interval = 1.0 / mpu_cfg.sample_rate

    if _SMBUS:
        try:
            bus = smbus2.SMBus(mpu_cfg.i2c_bus)
            _mpu_init(bus, mpu_cfg)
            print(f"  [MPU-6050] 수집 시작 ({duration}s, {mpu_cfg.sample_rate}Hz) ...")
            samples = []
            for _ in range(n):
                t0 = time.monotonic()
                samples.append(_read_accel(bus, mpu_cfg))
                sl = interval - (time.monotonic() - t0)
                if sl > 0:
                    time.sleep(sl)
            bus.close()
            print("  [MPU-6050] 수집 완료")
            sig = np.array(samples, dtype=np.float64)
        except Exception as e:
            print(f"  [MPU-6050] 오류({e}) → 시뮬레이션 신호 사용")
            sig = np.random.randn(n) * 0.02
    else:
        sig = np.random.randn(n) * 0.02

    # 저역통과 필터
    nyq = mpu_cfg.sample_rate / 2.0
    b, a = butter(4, min(mpu_cfg.lp_cutoff / nyq, 0.999), btype="low")
    sig = lfilter(b, a, sig)

    # 정규화
    pk = np.max(np.abs(sig))
    if pk > 1e-9:
        sig = sig / pk
    return sig


# =========================================================
# [3] 노이즈 데이터 수집 & 센서 융합
# =========================================================

class SensorDataCollector:
    """
    I2S 마이크 + MPU-6050 두 채널을 수집·융합한다.

    사용법:
        collector = SensorDataCollector(mic_cfg, mpu_cfg)
        fused_dsp = collector.collect(duration=8.0)
    """

    def __init__(self, mic_cfg: I2SMicConfig, mpu_cfg: MPU6050Config):
        self.mic_cfg    = mic_cfg
        self.mpu_cfg    = mpu_cfg
        self.mic_signal = None   # 다운샘플 후 마이크 신호 (DSP_FS)
        self.vib_signal = None   # 진동 센서 신호 (DSP_FS)
        self.fused      = None   # 융합 신호 (DSP_FS)
        self.stats      = {}

    def collect(
        self,
        duration   : float = 8.0,
        mic_weight : float = 0.6,
        _sim_signal: np.ndarray = None,
    ) -> np.ndarray:
        """
        두 센서를 수집 후 가중 합산.

        Parameters
        ----------
        duration    : 수집 시간 (초)
        mic_weight  : 마이크 가중치 (0~1), 나머지는 진동 센서
        _sim_signal : 시뮬레이션용 대체 신호 (DSP_FS 기준)
                      None 이면 실제 하드웨어 수집

        Returns
        -------
        fused : DSP_FS 기준 융합 신호
        """
        if _sim_signal is not None:
            self._process_sim(duration, mic_weight, _sim_signal)
        else:
            self._process_hw(duration, mic_weight)

        self._compute_stats()
        return self.fused

    # ── 실제 하드웨어 수집 ───────────────────────────────────
    def _process_hw(self, duration, mic_weight):
        mic_raw = capture_i2s_mic(self.mic_cfg, duration)
        vib_raw = capture_mpu6050(self.mpu_cfg, duration)

        # 마이크 다운샘플: 44100 → 1000 Hz
        nyq = MIC_FS / 2.0
        aa  = min((DSP_FS / 2.0 - 50) / nyq, 0.999)
        b, a = butter(8, aa, btype="low")
        mic_ds = lfilter(b, a, mic_raw)[::DOWNSAMPLE]

        n = min(len(mic_ds), len(vib_raw))
        self.mic_signal = mic_ds[:n]
        self.vib_signal = vib_raw[:n]
        self.fused = mic_weight * self.mic_signal + (1 - mic_weight) * self.vib_signal

    # ── 시뮬레이션 신호 처리 ────────────────────────────────
    def _process_sim(self, duration, mic_weight, sim):
        # 마이크 경로: 업샘플 → 대역통과 → 다운샘플
        nyq_mic = MIC_FS / 2.0
        ups = np.interp(
            np.linspace(0, len(sim) - 1, int(duration * MIC_FS)),
            np.arange(len(sim)), sim
        )
        b, a = butter(4, [20.0 / nyq_mic, min(420.0 / nyq_mic, 0.999)], btype="band")
        mic_bp = lfilter(b, a, ups)
        b2, a2 = butter(8, min((DSP_FS / 2.0 - 50) / nyq_mic, 0.999), btype="low")
        mic_ds = lfilter(b2, a2, mic_bp)[::DOWNSAMPLE]

        # 진동 센서 경로: 저역통과
        nyq_dsp = DSP_FS / 2.0
        b3, a3 = butter(4, min(200.0 / nyq_dsp, 0.999), btype="low")
        vib = lfilter(b3, a3, sim)

        n = min(len(mic_ds), len(vib))
        self.mic_signal = mic_ds[:n]
        self.vib_signal = vib[:n]
        self.fused = mic_weight * self.mic_signal + (1 - mic_weight) * self.vib_signal

    # ── 통계 계산 ────────────────────────────────────────────
    def _compute_stats(self):
        def _r(x): return float(np.sqrt(np.mean(x ** 2)))
        def _p(x): return float(np.max(np.abs(x)))

        ws   = int(0.2 * DSP_FS)
        wins = [_r(self.fused[i:i+ws]) for i in range(0, len(self.fused)-ws, ws)]
        act  = float(np.mean(np.array(wins) > 0.3 * max(wins))) if wins else 0.0

        self.stats = {
            "mic_rms":      round(_r(self.mic_signal), 4),
            "vib_rms":      round(_r(self.vib_signal), 4),
            "fused_rms":    round(_r(self.fused),      4),
            "fused_peak":   round(_p(self.fused),      4),
            "active_ratio": round(act,                 4),
        }

        if self.stats["fused_rms"] < 0.01:
            print("  [경고] 융합 신호 RMS가 너무 낮습니다. 센서 연결을 확인하세요.")
        if self.stats["active_ratio"] < 0.1:
            print("  [경고] 활성 구간이 짧습니다. 소음 발생 여부를 확인하세요.")

    def print_stats(self):
        print("\n  [센서 수집 통계]")
        for k, v in self.stats.items():
            print(f"    {k:20s}: {v}")


# =========================================================
# [4] 입력 신호 안정화
# =========================================================

class InputSignalStabilizer:
    """
    DSP 알고리즘 진입 전 신호 전처리.

    처리 순서:
      1) DC 오프셋 제거
      2) 진폭 정규화 (target_rms)
      3) 저역통과 전처리 필터
      4) 스파이크 클리핑
    """

    def __init__(
        self,
        fs              : int   = DSP_FS,
        target_rms      : float = 0.3,
        clip_threshold  : float = 2.5,
        prefilter_cutoff: float = 450.0,
    ):
        self.fs               = fs
        self.target_rms       = target_rms
        self.clip_threshold   = clip_threshold
        self.prefilter_cutoff = prefilter_cutoff

    def stabilize(self, signal: np.ndarray) -> np.ndarray:
        s = signal - np.mean(signal)                          # 1) DC 제거

        cur = float(np.sqrt(np.mean(s ** 2)))                 # 2) 정규화
        if cur > 1e-9:
            s = s * (self.target_rms / cur)

        nyq = self.fs / 2.0                                   # 3) LPF
        b, a = butter(4, min(self.prefilter_cutoff / nyq, 0.999), btype="low")
        s = lfilter(b, a, s)

        clip = self.target_rms * self.clip_threshold          # 4) 클리핑
        s = np.clip(s, -clip, clip)
        return s

    def __repr__(self):
        return (
            f"InputSignalStabilizer(target_rms={self.target_rms}, "
            f"clip={self.clip_threshold}x, LP={self.prefilter_cutoff}Hz)"
        )


# =========================================================
# 공개 인터페이스 — 메인 코드에서 이것만 호출
# =========================================================

def run_sensor_pipeline(
    raw_signal : np.ndarray = None,
    duration   : float      = 8.0,
    mic_weight : float      = 0.6,
    verbose    : bool       = True,
) -> np.ndarray:
    """
    이형규 센서 & 입력 전체 파이프라인.

    메인 코드(시뮬레이션) 연동:
        from sensor_input import run_sensor_pipeline
        input_signal = run_sensor_pipeline(raw_signal=sim_signal)

    실제 라즈베리파이 하드웨어 운용:
        input_signal = run_sensor_pipeline()   # raw_signal=None → 직접 수집

    Parameters
    ----------
    raw_signal  : 시뮬레이션용 DSP_FS 기준 신호.
                  None 이면 하드웨어에서 직접 수집.
    duration    : 수집 시간 (초)
    mic_weight  : 마이크 가중치 (0~1)
    verbose     : 통계 출력 여부

    Returns
    -------
    stabilized  : DSP_FS 기준 안정화된 입력 신호 (메인 코드 input_signal 대입)
    collector   : SensorDataCollector (그래프 그릴 때 채널별 신호 접근용)
    """

    mic_cfg = I2SMicConfig(
        sample_rate = MIC_FS,
        bit_depth   = 32,
        channels    = 1,
        device_name = "default",
        gain_db     = 6.0,
        hp_cutoff   = 20.0,
    )

    mpu_cfg = MPU6050Config(
        i2c_bus     = I2C_BUS,
        address     = MPU6050_ADDR,
        accel_range = MPU6050Config.RANGE_2G,
        sample_rate = DSP_FS,
        axis        = "z",
        lp_cutoff   = 200.0,
    )

    if verbose:
        print(f"\n  ★ [이형규] 센서 & 입력 파이프라인")
        print(f"    {mic_cfg}")
        print(f"    {mpu_cfg}")

    collector = SensorDataCollector(mic_cfg, mpu_cfg)
    collector.collect(
        duration    = duration,
        mic_weight  = mic_weight,
        _sim_signal = raw_signal,   # None 이면 실제 HW 수집
    )

    if verbose:
        collector.print_stats()

    stabilizer = InputSignalStabilizer(
        fs               = DSP_FS,
        target_rms       = 0.3,
        clip_threshold   = 2.5,
        prefilter_cutoff = 450.0,
    )
    stabilized = stabilizer.stabilize(collector.fused)

    if verbose:
        print(f"    안정화기: {stabilizer}")
        print(f"    안정화 후 RMS: {round(float(np.sqrt(np.mean(stabilized**2))), 4)}")

    return stabilized, collector