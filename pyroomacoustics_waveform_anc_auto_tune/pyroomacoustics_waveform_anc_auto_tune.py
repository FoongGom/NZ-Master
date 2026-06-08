"""
pyroomacoustics_waveform_anc_auto_tune.py

목적:
Waveform 기반 상쇄간섭 ANC 코드에서 latency_ms, gain, polarity를 자동으로 여러 값 테스트해서
상쇄가 가장 잘 되는 조합을 찾는 Pyroomacoustics 가상 방 시뮬레이션입니다.

왜 필요한가?
이전 결과에서 Reduction = -2.00 dB가 나왔습니다.
이건 상쇄 신호가 줄이는 방향이 아니라 오히려 소리를 키웠다는 뜻입니다.

상쇄간섭은 타이밍과 위상이 맞아야 하므로,
고정 latency_ms=120.0만 쓰면 실패할 수 있습니다.

이 코드는 아래 값을 자동으로 바꿔가며 가장 좋은 조합을 찾습니다.

1. gain
2. latency_ms
3. polarity

설치:
pip install numpy matplotlib scipy pyroomacoustics

실행:
python pyroomacoustics_waveform_anc_auto_tune.py
"""

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

try:
    import pyroomacoustics as pra
except ImportError:
    print("pyroomacoustics가 설치되어 있지 않습니다.")
    print("설치 명령어:")
    print("pip install pyroomacoustics")
    raise


# =========================================================
# 1. 가상 방 설정
# =========================================================

FS = 1000
DURATION = 2.0
N = int(FS * DURATION)
T = np.arange(N) / FS

ROOM_DIM = [5.0, 4.0, 2.6]

NOISE_POS = [2.5, 3.7, 2.2]
MIC_POS = [2.2, 2.0, 1.2]
SPEAKER_POS = [2.8, 2.0, 1.2]
LISTENER_POS = [2.5, 1.5, 1.2]

ABSORPTION = 0.45
MAX_ORDER = 0


# =========================================================
# 2. Waveform 기반 상쇄 처리기
# =========================================================

class WaveformANCProcessor:
    """
    입력 파형을 저역통과필터로 정리하고,
    delay로 타이밍을 맞춘 뒤,
    반대 위상으로 뒤집어 상쇄 신호를 만드는 클래스입니다.

    핵심:
    anti = polarity * gain * delayed

    polarity = -1 이면 기존 방식:
    anti = -gain * delayed

    polarity = +1 이면 반대 극성 테스트:
    anti = +gain * delayed

    왜 polarity를 테스트하나?
    가상 방/실제 하드웨어에서는 마이크, 스피커, 앰프, 경로 지연 때문에
    우리가 생각한 - 부호가 실제 청취 위치에서 항상 반대 위상으로 도착하지 않을 수 있습니다.
    그래서 -1과 +1을 둘 다 테스트해서 실제로 줄어드는 쪽을 찾습니다.
    """

    def __init__(
        self,
        fs=1000,
        gain=0.25,
        cutoff=250.0,
        latency_ms=120.0,
        polarity=-1.0,
        output_limit=1.0,
        min_rms=0.0005,
    ):
        self.fs = int(fs)
        self.gain = float(gain)
        self.cutoff = float(cutoff)
        self.latency_ms = float(latency_ms)
        self.polarity = float(polarity)
        self.output_limit = float(output_limit)
        self.min_rms = float(min_rms)

        self.dc = 0.0
        self.dc_alpha = 0.008

        nyquist = self.fs / 2.0
        self.sos = butter(4, self.cutoff / nyquist, btype="low", output="sos")
        self.zi = sosfilt_zi(self.sos) * 0.0

        self.max_delay_samples = int(self.fs * 0.3)
        self.delay_buffer = np.zeros(self.max_delay_samples, dtype=np.float32)
        self.delay_samples = int(round(self.fs * self.latency_ms / 1000.0))
        self.delay_samples = max(1, min(self.delay_samples, self.max_delay_samples - 1))

        self.write_index = 0

    def process_frame(self, frame):
        x = frame.astype(np.float32)

        block_mean = float(np.mean(x))
        self.dc = self.dc + self.dc_alpha * (block_mean - self.dc)
        x = x - self.dc

        mic_rms = float(np.sqrt(np.mean(x ** 2) + 1e-12))

        if mic_rms < self.min_rms:
            return np.zeros_like(x, dtype=np.float32)

        filtered, self.zi = sosfilt(self.sos, x, zi=self.zi)

        delayed = np.zeros_like(filtered)

        for i, sample in enumerate(filtered):
            read_index = (self.write_index - self.delay_samples) % self.max_delay_samples
            delayed[i] = self.delay_buffer[read_index]

            self.delay_buffer[self.write_index] = sample
            self.write_index = (self.write_index + 1) % self.max_delay_samples

        anti = self.polarity * self.gain * delayed
        anti = np.clip(anti, -self.output_limit, self.output_limit)

        return anti.astype(np.float32)

    def process_signal(self, signal, frame_size=128):
        out = np.zeros_like(signal, dtype=np.float32)

        for start in range(0, len(signal) - frame_size, frame_size):
            end = start + frame_size
            out[start:end] = self.process_frame(signal[start:end])

        return out


# =========================================================
# 3. 소음 생성
# =========================================================

def make_noise(kind="washing_machine"):
    if kind == "washing_machine":
        x = (
            0.80 * np.sin(2 * np.pi * 60 * T)
            + 0.35 * np.sin(2 * np.pi * 120 * T + 0.6)
            + 0.18 * np.sin(2 * np.pi * 180 * T + 1.2)
        )
        env = 0.75 + 0.25 * np.sin(2 * np.pi * 0.4 * T)
        return x * env

    if kind == "footstep":
        x = np.zeros_like(T)
        foot_times = [0.5, 1.1, 1.7]

        for ft in foot_times:
            idx = int(ft * FS)
            length = int(0.20 * FS)

            if idx + length >= len(x):
                continue

            tt = np.arange(length) / FS
            burst = np.exp(-tt * 22) * (
                np.sin(2 * np.pi * 55 * tt)
                + 0.5 * np.sin(2 * np.pi * 110 * tt)
            )
            x[idx:idx + length] += burst

        return x

    if kind == "voice_low":
        f0 = 130 + 30 * np.sin(2 * np.pi * 0.8 * T)
        phase = 2 * np.pi * np.cumsum(f0) / FS

        voice = (
            0.60 * np.sin(phase)
            + 0.25 * np.sin(2 * phase + 0.4)
            + 0.12 * np.sin(3 * phase + 1.0)
        )

        env = np.zeros_like(T)
        s = int(0.4 * FS)
        e = int(1.6 * FS)
        env[s:e] = 1.0

        smooth_len = int(0.04 * FS)
        smooth = np.ones(smooth_len) / smooth_len
        env = np.convolve(env, smooth, mode="same")

        return voice * env

    raise ValueError("unknown noise kind")


# =========================================================
# 4. Pyroomacoustics 방 전파
# =========================================================

def create_room():
    return pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
    )


def simulate_at_position(source_pos, source_signal, mic_pos):
    room = create_room()
    room.add_source(source_pos, signal=source_signal)
    room.add_microphone_array(np.c_[mic_pos])
    room.simulate()
    return room.mic_array.signals[0]


def simulate_before_after(noise_signal, anti_signal):
    before = simulate_at_position(NOISE_POS, noise_signal, LISTENER_POS)

    room = create_room()
    room.add_source(NOISE_POS, signal=noise_signal)
    room.add_source(SPEAKER_POS, signal=anti_signal)
    room.add_microphone_array(np.c_[LISTENER_POS])
    room.simulate()

    after = room.mic_array.signals[0]

    min_len = min(len(before), len(after))
    return before[:min_len], after[:min_len]


# =========================================================
# 5. 평가
# =========================================================

def rms(x):
    return np.sqrt(np.mean(x ** 2) + 1e-12)


def db_reduction(before, after):
    return 20 * np.log10((rms(before) + 1e-12) / (rms(after) + 1e-12))


def evaluate_setting(noise, mic_signal, gain, latency_ms, polarity, cutoff=250.0):
    processor = WaveformANCProcessor(
        fs=FS,
        gain=gain,
        cutoff=cutoff,
        latency_ms=latency_ms,
        polarity=polarity,
        output_limit=1.0,
        min_rms=0.0005,
    )

    anti_signal = processor.process_signal(mic_signal, frame_size=128)
    before, after = simulate_before_after(noise, anti_signal)
    reduction = db_reduction(before, after)

    return reduction, before, after, anti_signal


# =========================================================
# 6. 자동 튜닝 실행
# =========================================================

def auto_tune_case(kind="washing_machine"):
    print("=" * 80)
    print("Pyroomacoustics + Waveform ANC 자동 튜닝")
    print("=" * 80)
    print(f"CASE         : {kind}")
    print(f"DURATION     : {DURATION}")
    print(f"MAX_ORDER    : {MAX_ORDER}")
    print("=" * 80)

    noise = make_noise(kind)

    print("1) 마이크 위치 신호 계산 중...")
    mic_signal = simulate_at_position(NOISE_POS, noise, MIC_POS)

    # 빠르게 테스트할 후보값
    gain_list = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    latency_list = list(range(0, 161, 10))
    polarity_list = [-1.0, 1.0]

    best = None
    results = []

    print("2) latency / gain / polarity 자동 탐색 중...")

    total = len(gain_list) * len(latency_list) * len(polarity_list)
    count = 0

    for polarity in polarity_list:
        for gain in gain_list:
            for latency_ms in latency_list:
                count += 1

                reduction, before, after, anti = evaluate_setting(
                    noise=noise,
                    mic_signal=mic_signal,
                    gain=gain,
                    latency_ms=latency_ms,
                    polarity=polarity,
                    cutoff=250.0,
                )

                results.append((reduction, gain, latency_ms, polarity))

                if best is None or reduction > best[0]:
                    best = (reduction, gain, latency_ms, polarity, before, after, anti)

                print(
                    f"[{count:03d}/{total}] "
                    f"polarity={polarity:+.0f} | "
                    f"gain={gain:.2f} | "
                    f"latency={latency_ms:3d} ms | "
                    f"reduction={reduction:6.2f} dB"
                )

    results.sort(reverse=True, key=lambda x: x[0])

    print("\n" + "=" * 80)
    print("상위 10개 결과")
    print("=" * 80)

    for i, (reduction, gain, latency_ms, polarity) in enumerate(results[:10], start=1):
        print(
            f"{i:02d}. reduction={reduction:6.2f} dB | "
            f"gain={gain:.2f} | latency={latency_ms:3d} ms | polarity={polarity:+.0f}"
        )

    best_reduction, best_gain, best_latency, best_polarity, before, after, anti = best

    print("\n" + "=" * 80)
    print("최적 결과")
    print("=" * 80)
    print(f"Best reduction : {best_reduction:.2f} dB")
    print(f"Best gain      : {best_gain}")
    print(f"Best latency   : {best_latency} ms")
    print(f"Best polarity  : {best_polarity:+.0f}")
    print(f"Before RMS     : {rms(before):.6f}")
    print(f"After RMS      : {rms(after):.6f}")
    print("=" * 80)

    if best_reduction > 0:
        print("해석: 이 설정에서는 상쇄 후 소리가 줄어든 것입니다.")
    else:
        print("해석: 현재 후보값 안에서는 상쇄 성공 설정을 찾지 못했습니다.")

    print("\n실제 Raspberry Pi 코드에 반영할 후보값:")
    print(f"--gain {best_gain} --latency-ms {best_latency}")
    print("polarity가 +1이면 실제 코드에서 anti = -gain * delayed 대신 anti = +gain * delayed도 비교해보세요.")


def main():
    auto_tune_case("washing_machine")


if __name__ == "__main__":
    main()
