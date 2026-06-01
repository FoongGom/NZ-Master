
"""
실내 공기 중 층간소음 상쇄 시뮬레이션
Airborne Floor Noise ANC Simulation

목적:
- 위층에서 발생한 층간소음이 벽/천장 구조를 거쳐 아래층 실내 공기 중으로 퍼졌다고 가정한다.
- 마이크가 아래층 방 안에서 들리는 소리를 측정한다고 가정한다.
- 라즈베리파이가 DSP 알고리즘으로 반대 위상 제어 신호를 계산한다고 가정한다.
- 스피커가 반대 위상 소리를 출력하여 특정 위치에서 들리는 소리를 줄이는지 확인한다.

기존 방향:
- 바닥/천장/실험판의 구조 진동을 직접 줄이는 방향
- 이 경우 진동 액추에이터가 필요함

수정된 방향:
- 구조물 진동 자체를 직접 제어하지 않음
- 아래층 실내로 방사된 공기 중 소리를 마이크와 스피커로 상쇄하는 방향
- 이 경우 진동 액추에이터보다 마이크와 스피커가 핵심 부품임

하드웨어 적용 시 예상 구조:
마이크
→ Raspberry Pi
→ DSP 알고리즘
→ PCM5102 DAC
→ PAM8403 앰프
→ 스피커

주의:
- 이 코드는 실제 마이크/스피커를 바로 제어하는 실시간 하드웨어 코드는 아니다.
- 현재는 마이크로 들어온 소리를 가상 신호로 만들어 실험하는 시뮬레이션 코드이다.
- 실제 하드웨어 적용 시에는 generate_* 함수 대신 read_microphone_buffer() 같은 마이크 입력 함수로 바꿔야 한다.
- 스피커 기반 ANC는 특정 위치에서는 줄어들 수 있지만, 방 전체 모든 위치에서 동시에 줄이는 것은 어렵다.
"""


import os
import warnings

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from scipy.signal import butter, lfilter
from scipy.fft import rfft, rfftfreq


# =========================================================
# 0. 그래프 한글 폰트 설정
# =========================================================
# matplotlib 기본 폰트는 한글을 지원하지 않는 경우가 많음.
# Windows 기본 한글 폰트인 맑은 고딕을 직접 지정해서 한글 깨짐을 방지함.

font_path = "C:/Windows/Fonts/malgun.ttf"

if os.path.exists(font_path):
    font_name = fm.FontProperties(fname=font_path).get_name()
    plt.rcParams["font.family"] = font_name
else:
    plt.rcParams["font.family"] = "Malgun Gothic"

# 그래프에서 마이너스 기호가 깨지는 문제 방지
plt.rcParams["axes.unicode_minus"] = False

# 폰트 관련 경고가 너무 많이 출력되는 것을 방지
warnings.filterwarnings("ignore", category=UserWarning)


# =========================================================
# 1. 기본 설정
# =========================================================

# 샘플링 주파수
# 공기 중 소리 상쇄 방향에서는 사람 말소리의 낮은 성분까지 보기 위해 2000Hz로 설정
# fs = 2000이면 1초에 2000개의 데이터를 만든다는 뜻
fs = 2000

# 전체 시뮬레이션 시간
duration = 8

# 시간축 생성
# 0초부터 8초 전까지 0.001초 간격으로 생성
t = np.arange(0, duration, 1 / fs)

# 랜덤값 고정
# 실행할 때마다 같은 가상 소음이 만들어지도록 설정
np.random.seed(10)

# 그래프가 너무 많이 뜨면 False로 바꾸면 됨
SHOW_GRAPHS = True

# 앞 70%는 파라미터 탐색용, 뒤 30%는 평가용
# 실제 실험에서도 튜닝 구간과 평가 구간을 나누는 개념으로 볼 수 있음
TRAIN_RATIO = 0.7
train_end = int(len(t) * TRAIN_RATIO)
test_start = train_end


# =========================================================
# 2. 공통 DSP 유틸 함수
# =========================================================

def lowpass_filter(signal, cutoff, fs, order=4):
    """
    저역통과필터.

    역할:
    - cutoff 이하의 저주파 성분은 통과
    - cutoff 이상의 고주파 성분은 줄임

    층간소음에서는 왜 필요한가?
    - 발망치, 아이 뛰는 소리, 물건 낙하 등은 저주파 진동이 중요함
    - 고주파 잡음까지 제어하면 오히려 불안정해질 수 있음
    """

    b, a = butter(order, cutoff / (fs / 2), btype="low")
    return lfilter(b, a, signal)


def delay_signal(signal, delay_samples):
    """
    신호를 일정 샘플만큼 지연시키는 함수.

    예:
    fs = 1000Hz일 때
    1 sample = 1ms
    delay_samples = 25이면 25ms 지연

    상쇄간섭에서는 반대 위상 신호의 타이밍이 중요하므로 delay를 조절함.
    """

    if delay_samples <= 0:
        return signal.copy()

    delayed = np.zeros_like(signal)
    delayed[delay_samples:] = signal[:-delay_samples]

    return delayed


def rms(signal):
    """
    RMS 계산.

    RMS는 신호의 평균적인 세기를 나타냄.
    층간소음 실험에서는 RMS가 작아지면 진동/소음이 줄었다고 해석할 수 있음.
    """

    return np.sqrt(np.mean(signal ** 2))


def peak_abs(signal):
    """
    절댓값 기준 최대 피크값 계산.

    순간 충격이 얼마나 큰지 확인할 때 사용.
    """

    return np.max(np.abs(signal))


def db_reduction(before_signal, after_signal):
    """
    RMS 기준 감소량을 dB로 계산.

    양수: 제어 후 감소
    0: 변화 없음
    음수: 제어 후 오히려 증가
    """

    before_rms = rms(before_signal)
    after_rms = rms(after_signal)

    if after_rms == 0:
        return 999

    return 20 * np.log10(before_rms / after_rms)


def peak_db_reduction(before_signal, after_signal):
    """
    Peak 기준 감소량을 dB로 계산.

    순간 최대 충격이 줄었는지 확인할 때 사용.
    """

    before_peak = peak_abs(before_signal)
    after_peak = peak_abs(after_signal)

    if after_peak == 0:
        return 999

    return 20 * np.log10(before_peak / after_peak)


# =========================================================
# 3. 공기 중 스피커-마이크 전달 경로 모델
# =========================================================
# 현재 방향은 진동 액추에이터로 판을 직접 흔드는 것이 아니라,
# 스피커로 공기 중 반대 위상 소리를 출력하는 방식이다.
#
# 실제 시스템:
# 라즈베리파이 출력
# → PCM5102 DAC
# → PAM8403 앰프
# → 스피커
# → 방 안 공기 전달
# → 마이크/귀 위치에서 측정
#
# 이 과정에서 지연, 감쇠, 반사음이 발생한다.
# 여기서는 이 경로를 secondary path로 단순 모델링한다.

def apply_secondary_path(signal, secondary_path):
    """
    스피커에서 출력된 제어 소리가 방 안 공기를 거쳐 마이크/귀 위치에 도달한 결과를 계산.

    기존 단순 시뮬레이션:
    원래 신호 + 제어 신호

    현실 반영 시뮬레이션:
    원래 공기 중 소리 + 스피커-마이크 전달 경로를 통과한 제어 소리
    """

    return np.convolve(signal, secondary_path, mode="full")[:len(signal)]


def make_secondary_path(delay_samples=8, length=64):
    """
    단순화된 스피커-마이크 secondary path 생성.

    delay_samples:
    - 스피커 출력 소리가 마이크/귀 위치에 도달하기까지의 지연

    length:
    - 전달 경로 필터 길이
    """

    s = np.zeros(length)

    if delay_samples < length:
        s[delay_samples] = 0.75
    if delay_samples + 1 < length:
        s[delay_samples + 1] = 0.25
    if delay_samples + 2 < length:
        s[delay_samples + 2] = -0.10
    if delay_samples + 3 < length:
        s[delay_samples + 3] = 0.05

    total = np.sum(np.abs(s))

    if total > 0:
        s = s / total

    return s


# 실제 스피커-마이크 공기 전달 경로
secondary_actual = make_secondary_path(delay_samples=8, length=64)

# 알고리즘이 알고 있다고 가정하는 스피커-마이크 전달 경로 추정값
# 실제 경로와 일부러 조금 다르게 설정해서 현실성을 추가함
secondary_estimated = make_secondary_path(delay_samples=7, length=64)


# =========================================================
# 4. 실제 하드웨어 적용 시 바뀌는 부분
# =========================================================
# 현재 코드는 아래 generate_* 함수로 가상 소음을 만든다.
# 실제 라즈베리파이에 적용할 때는 이 부분이 마이크 입력으로 바뀌어야 한다.
#
# 예시 구조:
#
# def read_microphone_buffer():
#     """
#     실제 하드웨어 적용 시 사용할 함수 예시.
#     USB 마이크 또는 I2S 마이크에서 짧은 오디오 버퍼를 읽어오는 역할.
#     현재 시뮬레이션에서는 사용하지 않음.
#     """
#     pass
#
# 즉, 현재:
# input_signal = generate_washing_machine_noise(...)
#
# 실제 적용:
# input_signal = read_microphone_buffer()
#
# 로 바뀌어야 한다.


# =========================================================
# 4. 가상 층간소음 신호 생성 함수
# =========================================================

def generate_child_running_noise(t, fs, duration):
    """
    아기 뛰는 소리 생성.

    특징:
    - 짧은 간격으로 쿵쿵거림
    - 발걸음 간격이 불규칙함
    - 충격 후 짧은 저주파 잔향이 남음
    """

    signal = np.zeros_like(t)
    footstep_times = []
    current_time = 0.4

    while current_time < duration - 0.5:
        interval = np.random.uniform(0.25, 0.45)
        current_time += interval
        footstep_times.append(current_time)

    for step_time in footstep_times:
        index = int(step_time * fs)
        strength = np.random.uniform(0.8, 1.5)

        burst_len = int(0.25 * fs)

        if index + burst_len >= len(signal):
            burst_len = len(signal) - index

        if burst_len <= 0:
            continue

        burst_t = np.arange(burst_len) / fs
        envelope = np.exp(-18 * burst_t)

        mode_30hz = np.sin(2 * np.pi * 30 * burst_t)
        mode_55hz = 0.8 * np.sin(2 * np.pi * 55 * burst_t)
        mode_90hz = 0.4 * np.sin(2 * np.pi * 90 * burst_t)

        footstep_burst = strength * envelope * (
            mode_30hz + mode_55hz + mode_90hz
        )

        sharp_impact = np.zeros(burst_len)
        sharp_len = min(20, burst_len)
        sharp_impact[:sharp_len] = (
            strength * 1.8 * np.exp(-np.linspace(0, 4, sharp_len))
        )

        signal[index:index + burst_len] += footstep_burst + sharp_impact

    background = (
        0.12 * np.sin(2 * np.pi * 25 * t)
        + 0.08 * np.sin(2 * np.pi * 45 * t)
    )

    noise = 0.05 * np.random.randn(len(t))

    return signal + background + noise, footstep_times


def generate_adult_heavy_footstep_noise(t, fs, duration):
    """
    성인 발망치 소리 생성.

    특징:
    - 아기 뛰는 소리보다 간격이 김
    - 한 번의 충격이 더 묵직함
    - 20~60Hz 저주파 성분이 강함
    """

    signal = np.zeros_like(t)
    footstep_times = []
    current_time = 0.6

    while current_time < duration - 0.5:
        interval = np.random.uniform(0.55, 0.85)
        current_time += interval
        footstep_times.append(current_time)

    for step_time in footstep_times:
        index = int(step_time * fs)
        strength = np.random.uniform(1.3, 2.2)

        burst_len = int(0.35 * fs)

        if index + burst_len >= len(signal):
            burst_len = len(signal) - index

        if burst_len <= 0:
            continue

        burst_t = np.arange(burst_len) / fs
        envelope = np.exp(-10 * burst_t)

        mode_20hz = np.sin(2 * np.pi * 20 * burst_t)
        mode_35hz = 0.9 * np.sin(2 * np.pi * 35 * burst_t)
        mode_60hz = 0.5 * np.sin(2 * np.pi * 60 * burst_t)

        burst = strength * envelope * (mode_20hz + mode_35hz + mode_60hz)

        sharp = np.zeros(burst_len)
        sharp_len = min(25, burst_len)
        sharp[:sharp_len] = strength * 2.2 * np.exp(
            -np.linspace(0, 5, sharp_len)
        )

        signal[index:index + burst_len] += burst + sharp

    background = 0.08 * np.sin(2 * np.pi * 30 * t)
    noise = 0.04 * np.random.randn(len(t))

    return signal + background + noise, footstep_times


def generate_washing_machine_noise(t, fs, duration):
    """
    세탁기 진동 생성.

    특징:
    - 일정한 주파수 성분이 계속 반복됨
    - 반복 진동이므로 FxNLMS가 잘 작동하기 쉬움
    """

    signal = (
        0.8 * np.sin(2 * np.pi * 45 * t)
        + 0.5 * np.sin(2 * np.pi * 90 * t)
        + 0.25 * np.sin(2 * np.pi * 135 * t)
    )

    modulation = 1.0 + 0.2 * np.sin(2 * np.pi * 0.5 * t)
    signal = signal * modulation

    noise = 0.04 * np.random.randn(len(t))

    return signal + noise, []


def generate_chair_dragging_noise(t, fs, duration):
    """
    의자 끄는 소리 생성.

    특징:
    - 짧은 순간 충격보다는 일정 시간 이어지는 마찰 진동
    - 70Hz, 110Hz, 160Hz 성분을 포함
    - 연속 마찰음이므로 FxNLMS가 적응할 시간이 있음
    """

    signal = np.zeros_like(t)

    drag_sections = [
        (1.0, 2.0),
        (3.0, 3.8),
        (5.2, 6.4),
    ]

    for start, end in drag_sections:
        start_i = int(start * fs)
        end_i = int(end * fs)
        length = end_i - start_i

        if length <= 0:
            continue

        drag_t = np.arange(length) / fs

        vibration = (
            0.5 * np.sin(2 * np.pi * 70 * drag_t)
            + 0.35 * np.sin(2 * np.pi * 110 * drag_t)
            + 0.2 * np.sin(2 * np.pi * 160 * drag_t)
        )

        roughness = 1.0 + 0.5 * np.random.randn(length)
        roughness = np.clip(roughness, 0.2, 1.8)

        envelope = np.ones(length)
        fade_len = min(int(0.1 * fs), length // 2)

        if fade_len > 0:
            envelope[:fade_len] = np.linspace(0, 1, fade_len)
            envelope[-fade_len:] = np.linspace(1, 0, fade_len)

        signal[start_i:end_i] += vibration * roughness * envelope

    background = 0.06 * np.sin(2 * np.pi * 40 * t)
    noise = 0.06 * np.random.randn(len(t))

    return signal + background + noise, drag_sections


def generate_object_drop_noise(t, fs, duration):
    """
    물건 낙하 충격음 생성.

    특징:
    - 발생 횟수는 적음
    - 순간 피크가 매우 큼
    - 충격 이후 저주파 잔향이 남음
    """

    signal = np.zeros_like(t)

    drop_times = [1.2, 3.7, 6.1]

    for drop_time in drop_times:
        index = int(drop_time * fs)
        strength = np.random.uniform(2.0, 3.2)

        burst_len = int(0.6 * fs)

        if index + burst_len >= len(signal):
            burst_len = len(signal) - index

        if burst_len <= 0:
            continue

        burst_t = np.arange(burst_len) / fs
        envelope = np.exp(-7 * burst_t)

        mode_18hz = np.sin(2 * np.pi * 18 * burst_t)
        mode_40hz = 0.9 * np.sin(2 * np.pi * 40 * burst_t)
        mode_75hz = 0.5 * np.sin(2 * np.pi * 75 * burst_t)

        burst = strength * envelope * (mode_18hz + mode_40hz + mode_75hz)

        sharp = np.zeros(burst_len)
        sharp_len = min(35, burst_len)
        sharp[:sharp_len] = strength * 2.8 * np.exp(
            -np.linspace(0, 6, sharp_len)
        )

        signal[index:index + burst_len] += burst + sharp

    noise = 0.04 * np.random.randn(len(t))

    return signal + noise, drop_times



def generate_human_speech_noise(t, fs, duration):
    """
    사람 말소리 가상 신호 생성.

    특징:
    - 발소리나 물건 낙하처럼 순간적으로 발생하는 충격성 소음이 아님
    - 일정 시간 이어지는 연속 음성 소음으로 가정
    - 실제 사람 목소리는 더 넓은 주파수 대역을 가지지만,
      현재 시뮬레이션 fs=1000Hz 환경에서는 500Hz 이하 성분만 표현 가능함
    - 벽/바닥을 통해 둔탁하게 전달되는 낮은 말소리 성분을 중심으로 모델링함

    주의:
    - 이 함수는 실제 녹음된 사람 목소리가 아니라,
      말소리와 비슷한 주파수 변화와 진폭 변화를 가진 가상 신호임
    """

    signal = np.zeros_like(t)

    # 사람이 말하는 구간을 여러 구간으로 나눔
    # 실제 대화처럼 계속 말하다가 쉬고, 다시 말하는 상황을 가정
    speech_sections = [
        (0.8, 2.2),
        (3.0, 4.6),
        (5.4, 7.2),
    ]

    for start, end in speech_sections:
        start_i = int(start * fs)
        end_i = int(end * fs)

        if end_i > len(signal):
            end_i = len(signal)

        length = end_i - start_i

        if length <= 0:
            continue

        speech_t = np.arange(length) / fs

        # 사람 목소리의 낮은 주파수 성분을 단순 모델링
        # 120Hz, 180Hz, 240Hz, 320Hz 성분을 섞어 말소리 느낌을 만듦
        voice_base = (
            0.45 * np.sin(2 * np.pi * 120 * speech_t)
            + 0.30 * np.sin(2 * np.pi * 180 * speech_t)
            + 0.20 * np.sin(2 * np.pi * 240 * speech_t)
            + 0.12 * np.sin(2 * np.pi * 320 * speech_t)
        )

        # 말소리는 세기가 일정하지 않으므로 천천히 커졌다 작아지는 변화를 추가
        amplitude_modulation = (
            0.7
            + 0.25 * np.sin(2 * np.pi * 3.0 * speech_t)
            + 0.15 * np.sin(2 * np.pi * 5.5 * speech_t)
        )

        # 말소리 시작/끝이 갑자기 끊기지 않도록 fade in/out 적용
        envelope = np.ones(length)
        fade_len = min(int(0.15 * fs), length // 2)

        if fade_len > 0:
            envelope[:fade_len] = np.linspace(0, 1, fade_len)
            envelope[-fade_len:] = np.linspace(1, 0, fade_len)

        # 실제 말소리처럼 약간 불규칙한 세기 변화를 추가
        roughness = 1.0 + 0.15 * np.random.randn(length)
        roughness = np.clip(roughness, 0.6, 1.4)

        speech_signal = voice_base * amplitude_modulation * envelope * roughness

        signal[start_i:end_i] += speech_signal

    # 벽/바닥을 통해 전달되는 낮은 웅웅거림을 추가
    low_murmur = (
        0.08 * np.sin(2 * np.pi * 90 * t)
        + 0.05 * np.sin(2 * np.pi * 150 * t)
    )

    # 주변 잡음 추가
    noise = 0.035 * np.random.randn(len(t))

    return signal + low_murmur + noise, speech_sections


# =========================================================
# 5. 소음 유형 분류 함수
# =========================================================

def classify_noise(signal, fs):
    """
    소음 유형을 자동 분류하는 함수.

    분류 결과:
    - repetitive_vibration: 반복 진동
    - continuous_friction: 연속 마찰음
    - continuous_speech: 연속 음성 소음
    - impact_noise: 충격성 소음

    판단에 사용하는 특징:
    - peak/RMS 비율
    - 주요 주파수
    - 주요 주파수 비율
    - 활성 구간 비율
    """

    signal_rms = rms(signal)
    signal_peak = peak_abs(signal)
    peak_to_rms = signal_peak / (signal_rms + 1e-9)

    N = len(signal)
    xf = rfftfreq(N, 1 / fs)
    spectrum = np.abs(rfft(signal))

    low_freq_mask = (xf >= 5) & (xf <= 200)
    low_spectrum = spectrum[low_freq_mask]
    low_xf = xf[low_freq_mask]

    if len(low_spectrum) == 0:
        dominant_ratio = 0
        dominant_freq = 0
    else:
        dominant_peak = np.max(low_spectrum)
        total_energy = np.sum(low_spectrum) + 1e-9
        dominant_ratio = dominant_peak / total_energy
        dominant_freq = low_xf[np.argmax(low_spectrum)]

    window_size = int(0.2 * fs)
    window_rms_values = []

    for start in range(0, len(signal) - window_size, window_size):
        window = signal[start:start + window_size]
        window_rms_values.append(rms(window))

    window_rms_values = np.array(window_rms_values)

    if len(window_rms_values) > 0:
        active_ratio = np.mean(
            window_rms_values > 0.4 * np.max(window_rms_values)
        )
    else:
        active_ratio = 0

    if dominant_ratio > 0.20 and active_ratio > 0.70:
        noise_type = "repetitive_vibration"

    elif (
        active_ratio >= 0.65
        and peak_to_rms < 5.5
        and 80 <= dominant_freq <= 350
        and dominant_ratio < 0.12
    ):
        noise_type = "continuous_speech"

    elif (
        active_ratio >= 0.30
        and peak_to_rms < 7.0
        and 60 <= dominant_freq <= 180
    ):
        noise_type = "continuous_friction"

    else:
        noise_type = "impact_noise"

    features = {
        "peak_to_rms": peak_to_rms,
        "dominant_ratio": dominant_ratio,
        "dominant_freq": dominant_freq,
        "active_ratio": active_ratio,
    }

    return noise_type, features


# =========================================================
# 6. 제어 방식 1: Fixed Gain/Delay
# =========================================================

def fixed_gain_delay_control(input_signal, filtered_signal):
    """
    고정 이득/지연 제어.

    원리:
    - 입력 신호를 저역통과필터로 처리
    - delay를 적용해 타이밍을 맞춤
    - -gain을 곱해 반대 위상 신호 생성
    - secondary path를 통과시킨 뒤 원래 신호와 합성

    특징:
    - 구조가 단순함
    - 충격성 소음에서 FxNLMS보다 안정적인 경우가 있음
    """

    gain_values = np.arange(0.1, 1.1, 0.1)
    delay_values = range(0, 81)

    best_train_db = -999
    best_gain = 0
    best_delay = 0
    best_control_raw = None
    best_control_actual = None
    best_output = None

    for delay_samples in delay_values:
        delayed_signal = delay_signal(filtered_signal, delay_samples)

        for gain in gain_values:
            control_raw = -gain * delayed_signal
            control_actual = apply_secondary_path(control_raw, secondary_actual)
            output_signal = input_signal + control_actual

            train_db = db_reduction(
                input_signal[:train_end],
                output_signal[:train_end]
            )

            if train_db > best_train_db:
                best_train_db = train_db
                best_gain = gain
                best_delay = delay_samples
                best_control_raw = control_raw
                best_control_actual = control_actual
                best_output = output_signal

    test_db = db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    test_peak_db = peak_db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    control_ratio = (
        rms(best_control_raw[test_start:])
        / (rms(input_signal[test_start:]) + 1e-9)
    )

    return {
        "method": "Fixed Gain/Delay",
        "gain": best_gain,
        "delay": best_delay,
        "output": best_output,
        "control_raw": best_control_raw,
        "control_actual": best_control_actual,
        "test_after_rms": rms(best_output[test_start:]),
        "test_reduction_db": test_db,
        "test_peak_db": test_peak_db,
        "control_ratio": control_ratio,
        "train_db": best_train_db,
    }


# =========================================================
# 7. 제어 방식 2: FxNLMS Adaptive
# =========================================================

def run_fxnlms_control(
    input_signal,
    reference,
    filter_order,
    mu,
    control_limit=3.0,
    epsilon=1e-6
):
    """
    FxNLMS 적응형 제어 1회 실행.

    FxNLMS란?
    - Filtered-x Normalized LMS
    - 스피커/판 전달 경로를 고려하는 적응형 제어 방식

    장점:
    - 반복 진동, 연속 마찰음에 강함

    주의:
    - 순간 충격성 소음에는 오히려 불안정할 수 있음
    """

    w = np.zeros(filter_order)

    control_raw = np.zeros_like(input_signal)
    control_actual = np.zeros_like(input_signal)
    output_signal = np.zeros_like(input_signal)

    filtered_reference = apply_secondary_path(reference, secondary_estimated)

    for n in range(filter_order, len(input_signal)):
        x_vec = reference[n:n - filter_order:-1]
        xf_vec = filtered_reference[n:n - filter_order:-1]

        if len(x_vec) != filter_order or len(xf_vec) != filter_order:
            continue

        y_raw = -np.dot(w, x_vec)
        y_raw = np.clip(y_raw, -control_limit, control_limit)
        control_raw[n] = y_raw

        max_k = min(len(secondary_actual), n + 1)
        recent_control = control_raw[n:n - max_k:-1]
        coeff = secondary_actual[:len(recent_control)]

        y_actual = np.dot(coeff, recent_control)
        control_actual[n] = y_actual

        e = input_signal[n] + y_actual
        output_signal[n] = e

        norm_factor = epsilon + np.dot(xf_vec, xf_vec)
        w = w + (mu * e * xf_vec) / norm_factor

    output_signal[:filter_order] = input_signal[:filter_order]

    return control_raw, control_actual, output_signal, w


def fxnlms_adaptive_control(input_signal, filtered_signal):
    """
    FxNLMS의 filter_order와 mu를 자동 탐색.

    filter_order:
    - 적응형 필터가 참고하는 과거 샘플 개수

    mu:
    - 학습률
    - 크면 빠르게 적응하지만 불안정할 수 있음
    """

    filter_order_values = [16, 32, 64]
    mu_values = [0.001, 0.003, 0.005, 0.01, 0.02, 0.05]

    best_train_db = -999
    best_order = 0
    best_mu = 0
    best_control_raw = None
    best_control_actual = None
    best_output = None
    best_w = None

    for filter_order in filter_order_values:
        for mu in mu_values:
            control_raw, control_actual, output_signal, w = run_fxnlms_control(
                input_signal=input_signal,
                reference=filtered_signal,
                filter_order=filter_order,
                mu=mu,
                control_limit=3.0
            )

            train_db = db_reduction(
                input_signal[:train_end],
                output_signal[:train_end]
            )

            if train_db > best_train_db:
                best_train_db = train_db
                best_order = filter_order
                best_mu = mu
                best_control_raw = control_raw
                best_control_actual = control_actual
                best_output = output_signal
                best_w = w

    test_db = db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    test_peak_db = peak_db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    control_ratio = (
        rms(best_control_raw[test_start:])
        / (rms(input_signal[test_start:]) + 1e-9)
    )

    return {
        "method": "FxNLMS Adaptive",
        "filter_order": best_order,
        "mu": best_mu,
        "output": best_output,
        "control_raw": best_control_raw,
        "control_actual": best_control_actual,
        "test_after_rms": rms(best_output[test_start:]),
        "test_reduction_db": test_db,
        "test_peak_db": test_peak_db,
        "control_ratio": control_ratio,
        "train_db": best_train_db,
        "w": best_w,
    }


# =========================================================
# 8. 제어 방식 3: Ringdown Impact Control
# =========================================================

def ringdown_control(input_signal, cutoff=120):
    """
    충격 잔향 저감 제어.

    목적:
    - 첫 충격 자체는 너무 빨라서 능동제어로 줄이기 어려움
    - 충격 이후 남는 저주파 잔향을 줄이는 데 집중

    예:
    쿵!        → 첫 충격
    우우웅...  → 잔향 구간
    """

    low = lowpass_filter(input_signal, cutoff=cutoff, fs=fs)

    threshold = 2.5 * rms(input_signal)
    min_distance = int(0.25 * fs)

    impact_indices = []
    last_index = -min_distance

    for i in range(1, len(input_signal) - 1):
        if abs(input_signal[i]) > threshold:
            if i - last_index >= min_distance:
                impact_indices.append(i)
                last_index = i

    gain_values = [0.2, 0.3, 0.4, 0.5, 0.6]
    delay_values = range(0, 61)

    best_train_db = -999
    best_gain = 0
    best_delay = 0
    best_control_raw = None
    best_control_actual = None
    best_output = None

    for gain in gain_values:
        for delay in delay_values:
            control_raw = np.zeros_like(input_signal)

            for idx in impact_indices:
                start = idx + int(0.04 * fs)
                end = idx + int(0.50 * fs)

                if start >= len(input_signal):
                    continue

                end = min(end, len(input_signal))

                segment = low[start:end]
                delayed_segment = delay_signal(segment, delay)

                seg_len = len(delayed_segment)
                envelope = np.exp(-np.linspace(0, 3, seg_len))

                control_raw[start:end] += -gain * delayed_segment * envelope

            control_actual = apply_secondary_path(control_raw, secondary_actual)
            output_signal = input_signal + control_actual

            train_db = db_reduction(
                input_signal[:train_end],
                output_signal[:train_end]
            )

            if train_db > best_train_db:
                best_train_db = train_db
                best_gain = gain
                best_delay = delay
                best_control_raw = control_raw
                best_control_actual = control_actual
                best_output = output_signal

    test_db = db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    test_peak_db = peak_db_reduction(
        input_signal[test_start:],
        best_output[test_start:]
    )

    ringdown_before = []
    ringdown_after = []

    for idx in impact_indices:
        start = idx + int(0.04 * fs)
        end = idx + int(0.50 * fs)

        if start >= test_start and end <= len(input_signal):
            ringdown_before.extend(input_signal[start:end])
            ringdown_after.extend(best_output[start:end])

    if len(ringdown_before) > 0:
        ringdown_before = np.array(ringdown_before)
        ringdown_after = np.array(ringdown_after)
        ringdown_db = db_reduction(ringdown_before, ringdown_after)
    else:
        ringdown_db = test_db

    control_ratio = (
        rms(best_control_raw[test_start:])
        / (rms(input_signal[test_start:]) + 1e-9)
    )

    return {
        "method": "Ringdown Impact Control",
        "gain": best_gain,
        "delay": best_delay,
        "output": best_output,
        "control_raw": best_control_raw,
        "control_actual": best_control_actual,
        "test_after_rms": rms(best_output[test_start:]),
        "test_reduction_db": test_db,
        "test_peak_db": test_peak_db,
        "ringdown_db": ringdown_db,
        "impact_count": len(impact_indices),
        "control_ratio": control_ratio,
        "train_db": best_train_db,
    }


# =========================================================
# 9. 하이브리드 제어 선택
# =========================================================

def hybrid_control(input_signal, cutoff):
    """
    최종 하이브리드 제어 함수.

    소음 유형별 선택:
    - 반복 진동: FxNLMS
    - 연속 마찰음: FxNLMS
    - 연속 음성 소음: FxNLMS
    - 충격성 소음: Fixed와 Ringdown 중 RMS 감소량이 큰 방식
    """

    filtered_signal = lowpass_filter(input_signal, cutoff=cutoff, fs=fs)

    noise_type, features = classify_noise(input_signal, fs)

    fixed_result = fixed_gain_delay_control(input_signal, filtered_signal)
    fxnlms_result = fxnlms_adaptive_control(input_signal, filtered_signal)
    ringdown_result = ringdown_control(input_signal, cutoff=cutoff)

    if noise_type == "repetitive_vibration":
        selected = fxnlms_result
        selected_mode = "FxNLMS Adaptive"

    elif noise_type == "continuous_friction":
        selected = fxnlms_result
        selected_mode = "FxNLMS Adaptive"

    elif noise_type == "continuous_speech":
        selected = fxnlms_result
        selected_mode = "FxNLMS Adaptive"

    else:
        if fixed_result["test_reduction_db"] >= ringdown_result["test_reduction_db"]:
            selected = fixed_result
            selected_mode = "Fixed Gain/Delay"
        else:
            selected = ringdown_result
            selected_mode = "Ringdown Impact Control"

    candidates = [
        ("Fixed Gain/Delay", fixed_result),
        ("FxNLMS Adaptive", fxnlms_result),
        ("Ringdown Impact Control", ringdown_result),
    ]

    best_name, best_result = max(
        candidates,
        key=lambda item: item[1]["test_reduction_db"]
    )

    return {
        "noise_type": noise_type,
        "features": features,
        "selected_mode": selected_mode,
        "selected_result": selected,
        "best_name": best_name,
        "best_result": best_result,
        "fixed_result": fixed_result,
        "fxnlms_result": fxnlms_result,
        "ringdown_result": ringdown_result,
    }


# =========================================================
# 10. 실험 실행 함수
# =========================================================

def run_experiment(name, input_signal, event_info, cutoff=150, show_graph=True):
    """
    하나의 소음 시나리오에 대해 전체 실험을 실행.

    실행 순서:
    1. 소음 유형 분류
    2. Fixed 제어 실행
    3. FxNLMS 제어 실행
    4. Ringdown 제어 실행
    5. 하이브리드 방식 선택
    6. 결과 출력 및 그래프 표시
    """

    result = hybrid_control(input_signal, cutoff=cutoff)

    noise_type = result["noise_type"]
    features = result["features"]

    fixed_result = result["fixed_result"]
    fxnlms_result = result["fxnlms_result"]
    ringdown_result = result["ringdown_result"]

    selected_result = result["selected_result"]
    selected_mode = result["selected_mode"]

    best_name = result["best_name"]
    best_result = result["best_result"]

    test_before = input_signal[test_start:]

    korean_name_map = {
        "Child Running Noise": "아기 뛰는 소리",
        "Adult Heavy Footstep Noise": "성인 발망치",
        "Washing Machine Vibration": "세탁기 진동",
        "Chair Dragging Noise": "의자 끄는 소리",
        "Object Drop Impact Noise": "물건 낙하 충격음",
        "Human Speech Noise": "사람 말소리",
    }

    korean_type_map = {
        "impact_noise": "충격성 소음",
        "repetitive_vibration": "반복 진동",
        "continuous_friction": "연속 마찰음",
        "continuous_speech": "연속 음성 소음",
    }

    korean_method_map = {
        "Fixed Gain/Delay": "고정 이득/지연 제어",
        "FxNLMS Adaptive": "FxNLMS 적응형 제어",
        "Ringdown Impact Control": "충격 잔향 저감 제어",
    }

    name_kr = korean_name_map.get(name, name)
    type_kr = korean_type_map.get(noise_type, noise_type)
    selected_kr = korean_method_map.get(selected_mode, selected_mode)
    best_kr = korean_method_map.get(best_name, best_name)

    print()
    print("=" * 90)
    print(f"[실험] {name} ({name_kr})")
    print("=" * 90)
    print("저역통과필터 cutoff:", cutoff, "Hz")
    print("이벤트 정보:", event_info)
    print("평가 구간 제어 전 RMS:", rms(test_before))

    print()
    print("[소음 유형 분류]")
    print("분류 결과:", noise_type, f"({type_kr})")
    print("peak/RMS:", round(features["peak_to_rms"], 3), "(피크/RMS 비율)")
    print("dominant frequency:", round(features["dominant_freq"], 3), "Hz", "(주요 주파수)")
    print("dominant ratio:", round(features["dominant_ratio"], 4), "(주요 주파수 비율)")
    print("active ratio:", round(features["active_ratio"], 4), "(활성 구간 비율)")

    print()
    print("[고정 gain/delay 방식 (고정 이득/지연 제어)]")
    print("RMS 감소량 dB:", fixed_result["test_reduction_db"])
    print("Peak 감소량 dB:", fixed_result["test_peak_db"])
    print("gain:", fixed_result["gain"], "(출력 세기)")
    print("delay:", fixed_result["delay"], "(지연 샘플)")
    print("제어비:", fixed_result["control_ratio"])

    print()
    print("[FxNLMS 적응형 방식 (FxNLMS 적응형 제어)]")
    print("RMS 감소량 dB:", fxnlms_result["test_reduction_db"])
    print("Peak 감소량 dB:", fxnlms_result["test_peak_db"])
    print("filter_order:", fxnlms_result["filter_order"], "(필터 길이)")
    print("mu:", fxnlms_result["mu"], "(학습률)")
    print("제어비:", fxnlms_result["control_ratio"])

    print()
    print("[충격 잔향 저감 방식 (Ringdown Impact Control)]")
    print("RMS 감소량 dB:", ringdown_result["test_reduction_db"])
    print("Peak 감소량 dB:", ringdown_result["test_peak_db"])
    print("잔향 구간 감소량 dB:", ringdown_result["ringdown_db"])
    print("감지된 충격 수:", ringdown_result["impact_count"])
    print("gain:", ringdown_result["gain"], "(출력 세기)")
    print("delay:", ringdown_result["delay"], "(지연 샘플)")
    print("제어비:", ringdown_result["control_ratio"])

    print()
    print("[하이브리드 선택 결과]")
    print("선택된 제어 방식:", selected_mode, f"({selected_kr})")
    print("선택 방식 RMS 감소량 dB:", selected_result["test_reduction_db"])
    print("선택 방식 Peak 감소량 dB:", selected_result["test_peak_db"])

    print()
    print("[참고: 실제 최고 성능 방식]")
    print("최고 성능 방식:", best_name, f"({best_kr})")
    print("최고 성능 RMS 감소량 dB:", best_result["test_reduction_db"])

    if show_graph:
        plt.figure(figsize=(12, 10))

        plt.subplot(5, 1, 1)
        plt.plot(t, input_signal)
        plt.axvline(
            t[train_end],
            linestyle="--",
            label="Train/Test Split (학습/평가 구간 경계)"
        )
        plt.title(f"{name} ({name_kr}) - Input Signal (입력 신호)")
        plt.xlabel("Time [s] (시간 [초])")
        plt.ylabel("Amplitude (진폭)")
        plt.legend()

        plt.subplot(5, 1, 2)
        plt.plot(t, fixed_result["output"])
        plt.axvline(t[train_end], linestyle="--")
        plt.title(
            f"Fixed Gain/Delay (고정 이득/지연 제어) "
            f"({fixed_result['test_reduction_db']:.2f} dB)"
        )
        plt.xlabel("Time [s] (시간 [초])")
        plt.ylabel("Amplitude (진폭)")

        plt.subplot(5, 1, 3)
        plt.plot(t, fxnlms_result["output"])
        plt.axvline(t[train_end], linestyle="--")
        plt.title(
            f"FxNLMS Adaptive (FxNLMS 적응형 제어) "
            f"({fxnlms_result['test_reduction_db']:.2f} dB)"
        )
        plt.xlabel("Time [s] (시간 [초])")
        plt.ylabel("Amplitude (진폭)")

        plt.subplot(5, 1, 4)
        plt.plot(t, ringdown_result["output"])
        plt.axvline(t[train_end], linestyle="--")
        plt.title(
            f"Ringdown Impact Control (충격 잔향 저감 제어) "
            f"({ringdown_result['test_reduction_db']:.2f} dB, "
            f"Ringdown (잔향) {ringdown_result['ringdown_db']:.2f} dB)"
        )
        plt.xlabel("Time [s] (시간 [초])")
        plt.ylabel("Amplitude (진폭)")

        plt.subplot(5, 1, 5)
        plt.plot(t, selected_result["control_actual"])
        plt.axvline(t[train_end], linestyle="--")
        plt.title(
            f"Selected Actual Control Signal "
            f"(선택된 실제 제어 신호): {selected_mode} ({selected_kr})"
        )
        plt.xlabel("Time [s] (시간 [초])")
        plt.ylabel("Amplitude (진폭)")

        plt.tight_layout()
        plt.show()

        test_input = input_signal[test_start:]
        test_fixed = fixed_result["output"][test_start:]
        test_fx = fxnlms_result["output"][test_start:]
        test_ring = ringdown_result["output"][test_start:]
        test_selected = selected_result["output"][test_start:]

        xf_test = rfftfreq(len(test_input), 1 / fs)

        before_fft = np.abs(rfft(test_input))
        fixed_fft = np.abs(rfft(test_fixed))
        fx_fft = np.abs(rfft(test_fx))
        ring_fft = np.abs(rfft(test_ring))
        selected_fft = np.abs(rfft(test_selected))

        plt.figure(figsize=(12, 5))
        plt.plot(xf_test, before_fft, label="Before Control (제어 전)")
        plt.plot(xf_test, fixed_fft, label="Fixed (고정형)")
        plt.plot(xf_test, fx_fft, label="FxNLMS (적응형)")
        plt.plot(xf_test, ring_fft, label="Ringdown (잔향 저감)")
        plt.plot(
            xf_test,
            selected_fft,
            label="Selected Hybrid (선택된 하이브리드)",
            linewidth=2
        )
        plt.xlim(0, 200)
        plt.title(f"{name} ({name_kr}) - FFT Comparison (주파수 분석 비교)")
        plt.xlabel("Frequency [Hz] (주파수 [Hz])")
        plt.ylabel("Magnitude (크기)")
        plt.legend()
        plt.grid()
        plt.show()

    return {
        "name": name,
        "name_kr": name_kr,
        "noise_type": noise_type,
        "noise_type_kr": type_kr,
        "test_before_rms": rms(test_before),

        "fixed_db": fixed_result["test_reduction_db"],
        "fxnlms_db": fxnlms_result["test_reduction_db"],
        "ringdown_db": ringdown_result["test_reduction_db"],
        "ringdown_only_db": ringdown_result["ringdown_db"],

        "selected_mode": selected_mode,
        "selected_mode_kr": selected_kr,
        "selected_db": selected_result["test_reduction_db"],
        "selected_peak_db": selected_result["test_peak_db"],

        "best_name": best_name,
        "best_name_kr": best_kr,
        "best_db": best_result["test_reduction_db"],
    }


# =========================================================
# 11. 여러 시나리오 실행
# =========================================================

experiments = []

child_signal, child_events = generate_child_running_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Child Running Noise",
        input_signal=child_signal,
        event_info=[round(x, 3) for x in child_events],
        cutoff=150,
        show_graph=SHOW_GRAPHS
    )
)

adult_signal, adult_events = generate_adult_heavy_footstep_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Adult Heavy Footstep Noise",
        input_signal=adult_signal,
        event_info=[round(x, 3) for x in adult_events],
        cutoff=120,
        show_graph=SHOW_GRAPHS
    )
)

washing_signal, washing_events = generate_washing_machine_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Washing Machine Vibration",
        input_signal=washing_signal,
        event_info="continuous vibration",
        cutoff=180,
        show_graph=SHOW_GRAPHS
    )
)

chair_signal, chair_events = generate_chair_dragging_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Chair Dragging Noise",
        input_signal=chair_signal,
        event_info=chair_events,
        cutoff=200,
        show_graph=SHOW_GRAPHS
    )
)

drop_signal, drop_events = generate_object_drop_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Object Drop Impact Noise",
        input_signal=drop_signal,
        event_info=drop_events,
        cutoff=120,
        show_graph=SHOW_GRAPHS
    )
)

speech_signal, speech_events = generate_human_speech_noise(t, fs, duration)
experiments.append(
    run_experiment(
        name="Human Speech Noise",
        input_signal=speech_signal,
        event_info=speech_events,
        cutoff=600,
        show_graph=SHOW_GRAPHS
    )
)


# =========================================================
# 12. 전체 결과 요약표 출력
# =========================================================

print()
print()
print("=" * 160)
print("전체 실험 결과 요약 - 실내 공기 중 층간소음 상쇄")
print("=" * 160)
print(
    "소음 종류".ljust(34),
    "| 분류".ljust(30),
    "| Fixed dB".rjust(11),
    "| FxNLMS dB".rjust(12),
    "| Ring dB".rjust(10),
    "| Ring잔향 dB".rjust(14),
    "| 선택 방식".ljust(36),
    "| 선택 dB".rjust(10),
    "| 최고 방식".ljust(28),
    "| 최고 dB".rjust(9),
)
print("-" * 160)

for result in experiments:
    noise_name = f"{result['name']} ({result['name_kr']})"
    noise_type = f"{result['noise_type']} ({result['noise_type_kr']})"
    selected_mode = f"{result['selected_mode']} ({result['selected_mode_kr']})"
    best_mode = f"{result['best_name']} ({result['best_name_kr']})"

    print(
        noise_name.ljust(34),
        f"| {noise_type}".ljust(30),
        f"| {result['fixed_db']:9.3f}",
        f"| {result['fxnlms_db']:10.3f}",
        f"| {result['ringdown_db']:8.3f}",
        f"| {result['ringdown_only_db']:12.3f}",
        f"| {selected_mode}".ljust(36),
        f"| {result['selected_db']:8.3f}",
        f"| {best_mode}".ljust(28),
        f"| {result['best_db']:7.3f}",
    )

print("=" * 160)


# =========================================================
# 13. 전체 결과 막대그래프 출력
# =========================================================

names = [f"{result['name']}\n({result['name_kr']})" for result in experiments]

fixed_values = [result["fixed_db"] for result in experiments]
fx_values = [result["fxnlms_db"] for result in experiments]
ring_values = [result["ringdown_db"] for result in experiments]
selected_values = [result["selected_db"] for result in experiments]

x = np.arange(len(names))
width = 0.2

plt.figure(figsize=(14, 6))

plt.bar(
    x - 1.5 * width,
    fixed_values,
    width,
    label="Fixed Gain/Delay (고정 이득/지연)"
)

plt.bar(
    x - 0.5 * width,
    fx_values,
    width,
    label="FxNLMS Adaptive (적응형)"
)

plt.bar(
    x + 0.5 * width,
    ring_values,
    width,
    label="Ringdown (충격 잔향)"
)

plt.bar(
    x + 1.5 * width,
    selected_values,
    width,
    label="Selected Hybrid (선택된 하이브리드)"
)

plt.xticks(x, names, rotation=20)
plt.ylabel("Reduction [dB] (감소량 [dB])")
plt.title("Airborne Floor Noise ANC Result by Noise Type (공기 중 층간소음 상쇄 결과)")
plt.legend()
plt.grid(axis="y")
plt.tight_layout()
plt.show()
