# real_time/anc_controller.py
import numpy as np
from collections import deque
from scipy.signal import butter, lfilter
from scipy.fft import rfft, rfftfreq

class ANC_Controller:
    def __init__(self, fs=16000, buffer_size=256):
        self.fs = fs
        self.buffer_size = buffer_size
        self.recent_buffer = deque(maxlen=buffer_size * 8)

        self.gain = 0.52          # 안전하게 시작
        self.delay_samples = 12
        self.feedback_suppression = 0.70
        self.w = np.zeros(48)
        self.mu = 0.0025

    def lowpass(self, signal, cutoff=160):
        b, a = butter(4, cutoff / (self.fs / 2), btype="low")
        return lfilter(b, a, signal)

    def classify_noise(self, signal):
        signal = np.array(signal, dtype=np.float32)
        rms_val = np.sqrt(np.mean(signal**2) + 1e-12)
        peak_to_rms = np.max(np.abs(signal)) / (rms_val + 1e-9)

        spectrum = np.abs(rfft(signal))
        dominant_freq = np.argmax(spectrum[:len(spectrum)//5]) * self.fs / len(signal)

        if peak_to_rms > 7.5:
            return "impact"
        elif 40 < dominant_freq < 250:
            return "continuous"
        else:
            return "impulse"

    def process(self, raw_data):
        self.recent_buffer.extend(raw_data)
        signal = np.array(raw_data, dtype=np.float32)

        noise_type = self.classify_noise(signal)
        filtered = self.lowpass(signal)

        if noise_type == "continuous":
            control = self._fxnlms_process(filtered)
            method = "FxNLMS"
        else:
            control = self._fixed_process(filtered)
            method = "Fixed"

        control = control * self.feedback_suppression

        return {
            "method": method,
            "noise_type": noise_type,
            "gain": round(self.gain, 3),
            "delay": self.delay_samples,
            "control_signal": control.tolist(),
            "estimated_db": self._estimate_db(signal, signal + control)
        }

    def _fixed_process(self, signal):
        return -self.gain * np.roll(signal, self.delay_samples)

    def _fxnlms_process(self, signal):
        control = np.zeros_like(signal)
        for n in range(len(self.w), len(signal)):
            x = signal[n:n-len(self.w):-1]
            if len(x) != len(self.w): continue
            y = -np.dot(self.w, x)
            control[n] = y
            error = signal[n] + y
            self.w += self.mu * error * x / (np.dot(x, x) + 1e-8)
        return control

    def _estimate_db(self, before, after):
        b_rms = np.sqrt(np.mean(before**2) + 1e-12)
        a_rms = np.sqrt(np.mean(after**2) + 1e-12)
        return 20 * np.log10(b_rms / a_rms) if a_rms > 0 else 999
