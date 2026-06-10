#!/usr/bin/env python3
"""
============================================================
 ANC Server - Raspberry Pi 4B+
 3-Way Competitive ANC Engine
============================================================
 알고리즘 3종 동시 연산 → 매 청크 최고 상쇄율 방식 선택
   1. Fixed Gain Delay       (고정 딜레이 + 위상 반전)
   2. FxNLMS                 (적응 필터 + 이차경로 추정)
   3. Ringdown Impact Control (충격음 감지 + 잔향 제어)

 통신:
   ESP32 → RPi : 16bit PCM 소음 데이터
   RPi  → ESP32: 16bit PCM 상쇄음 데이터 (승리 방식)
============================================================
"""

import asyncio
import numpy as np
import logging
import time
import signal
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Tuple

# ──────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────
HOST             = "0.0.0.0"
PORT             = 5000
SAMPLE_RATE      = 16000
CHUNK_SIZE       = 256
BYTES_PER_SAMPLE = 2
CHUNK_BYTES      = CHUNK_SIZE * BYTES_PER_SAMPLE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ANC")


# ──────────────────────────────────────────
# 공용 유틸
# ──────────────────────────────────────────
def cancellation_rate(original: np.ndarray, anti: np.ndarray) -> float:
    """
    상쇄율 계산
      = 1 - (원본 + 상쇄음)의 전력 / 원본 전력
    1.0 = 완전 상쇄, 0.0 = 효과 없음, 음수 = 악화
    """
    in_pwr = float(np.mean(original ** 2))
    if in_pwr < 1e-12:
        return 0.0
    residual_pwr = float(np.mean((original + anti) ** 2))
    return max(-1.0, 1.0 - residual_pwr / in_pwr)


# ══════════════════════════════════════════
# 방식 1: Fixed Gain Delay
# ══════════════════════════════════════════
class FixedGainDelay:
    """
    원리:
      소음을 N샘플 지연시킨 뒤 위상 반전(×-gain) 출력
      딜레이는 마이크~스피커 물리적 거리에 해당하는 샘플 수
      gain은 음향 경로 감쇠를 보정하는 고정 계수

    장점: 연산량 극소, 레이턴시 최저
    단점: 환경 변화에 무감각 (고정값이므로)
    최적 소음: 단순 주기 진동음 (기계 진동, 팬 소음)
    """

    # 딜레이 후보 범위: 0~10ms (0~160샘플 @ 16kHz)
    DELAY_MIN  =  0
    DELAY_MAX  = 160
    DELAY_STEP =  8    # 8샘플 간격으로 탐색
    GAIN_MIN   = 0.5
    GAIN_MAX   = 1.5
    GAIN_STEP  = 0.1
    ADAPT_RATE = 50    # N청크마다 파라미터 재탐색

    def __init__(self):
        self.delay_samples = 32     # 초기 딜레이 (2ms)
        self.gain          = 1.0    # 초기 게인
        self._buf          = np.zeros(self.DELAY_MAX + CHUNK_SIZE, dtype=np.float64)
        self._chunk_count  = 0
        self._best_rate    = -999.0
        self.name          = "FixedGainDelay"

    def process(self, x: np.ndarray) -> Tuple[np.ndarray, float]:
        # 입력 버퍼 슬라이딩
        buf_len = len(self._buf)
        self._buf = np.roll(self._buf, -len(x))
        self._buf[-len(x):] = x

        # 현재 파라미터로 상쇄음 생성
        anti = self._make_anti(x, self.delay_samples, self.gain)
        rate = cancellation_rate(x, anti)

        # 주기적으로 최적 (delay, gain) 탐색
        self._chunk_count += 1
        if self._chunk_count % self.ADAPT_RATE == 0:
            self._search_params(x)

        return anti, rate

    def _make_anti(self, x: np.ndarray, delay: int, gain: float) -> np.ndarray:
        """딜레이 적용 후 반전"""
        buf = self._buf
        start = len(buf) - len(x) - delay
        start = max(0, start)
        delayed = buf[start: start + len(x)]
        if len(delayed) < len(x):
            delayed = np.pad(delayed, (len(x) - len(delayed), 0))
        return -gain * delayed

    def _search_params(self, x: np.ndarray):
        """그리드 탐색으로 최적 (delay, gain) 갱신"""
        best_rate  = -999.0
        best_delay = self.delay_samples
        best_gain  = self.gain

        for d in range(self.DELAY_MIN, self.DELAY_MAX + 1, self.DELAY_STEP):
            for g in np.arange(self.GAIN_MIN, self.GAIN_MAX + 0.01, self.GAIN_STEP):
                anti = self._make_anti(x, d, g)
                r    = cancellation_rate(x, anti)
                if r > best_rate:
                    best_rate  = r
                    best_delay = d
                    best_gain  = g

        if best_rate > self._best_rate - 0.05:  # 5% 이상 개선될 때만 갱신
            self.delay_samples = best_delay
            self.gain          = best_gain
            self._best_rate    = best_rate


# ══════════════════════════════════════════
# 방식 2: FxNLMS (Filtered-x Normalized LMS)
# ══════════════════════════════════════════
class FxNLMS:
    """
    원리:
      표준 LMS에 두 가지 개선을 추가:
        1. Filtered-x: 이차경로(스피커→공간→마이크) 추정 필터를 통해
                       입력 신호를 사전 필터링, 음향 경로 왜곡 보정
        2. Normalized: 입력 신호 전력으로 학습률을 정규화
                       신호 크기가 변해도 안정적으로 수렴

    장점: 음향 경로 변화에 적응, 높은 상쇄율
    단점: LMS 대비 연산 2배
    최적 소음: 복합 주파수 소음, 반사음이 많은 환경
    """

    def __init__(self, order: int = 64, mu: float = 0.01,
                 sec_path_order: int = 16):
        self.order          = order
        self.mu             = mu
        self.w              = np.zeros(order,          dtype=np.float64)  # 제어 필터
        self.s_hat          = np.zeros(sec_path_order, dtype=np.float64)  # 이차경로 추정
        self.s_hat[0]       = 1.0   # 초기: 단위 임펄스 (경로 없음 가정)
        self.x_buf          = np.zeros(order,          dtype=np.float64)  # 입력 버퍼
        self.fx_buf         = np.zeros(order,          dtype=np.float64)  # 필터링된 입력 버퍼
        self.sec_path_order = sec_path_order
        self._sec_buf       = np.zeros(sec_path_order, dtype=np.float64)  # 이차경로 버퍼
        self._err_buf       = deque(maxlen=200)
        self._adapt_count   = 0
        self.name           = "FxNLMS"

    def process(self, x: np.ndarray) -> Tuple[np.ndarray, float]:
        out = np.empty(len(x), dtype=np.float64)

        for i, x_n in enumerate(x):
            # ① 입력 버퍼 갱신
            self.x_buf  = np.roll(self.x_buf,  1); self.x_buf[0]  = x_n

            # ② 이차경로 필터 적용 (Filtered-x)
            fx_n = np.dot(self.s_hat, self._sec_buf if len(self._sec_buf) == self.sec_path_order
                          else np.zeros(self.sec_path_order))
            self._sec_buf = np.roll(self._sec_buf, 1); self._sec_buf[0] = x_n
            fx_n = np.dot(self.s_hat, self._sec_buf)

            self.fx_buf = np.roll(self.fx_buf, 1); self.fx_buf[0] = fx_n

            # ③ 제어 필터 출력
            y_n   = np.dot(self.w, self.x_buf)
            anti  = -y_n
            out[i] = anti

            # ④ 오차 = 원본 + 상쇄음 (잔류 소음)
            e_n = x_n + anti

            # ⑤ Normalized LMS 계수 갱신
            norm = np.dot(self.fx_buf, self.fx_buf) + 1e-8
            self.w += (self.mu / norm) * e_n * self.fx_buf

            self._err_buf.append(e_n ** 2)

        rate = cancellation_rate(x, out)

        # 이차경로 온라인 추정 (500청크마다 갱신)
        self._adapt_count += 1
        if self._adapt_count % 500 == 0:
            self._update_sec_path(x, out)

        return out, rate

    def _update_sec_path(self, x: np.ndarray, anti: np.ndarray):
        """
        간이 이차경로 추정:
        상쇄음과 잔류 소음의 상관관계로 경로 임펄스 응답 갱신
        """
        if np.std(anti) < 1e-8:
            return
        corr = np.correlate(x + anti, anti, mode='full')
        mid  = len(corr) // 2
        h    = corr[mid: mid + self.sec_path_order]
        h   /= (np.max(np.abs(h)) + 1e-8)
        # 소폭 갱신 (급격한 변화 방지)
        self.s_hat = 0.95 * self.s_hat + 0.05 * h


# ══════════════════════════════════════════
# 방식 3: Ringdown Impact Control
# ══════════════════════════════════════════
class RingdownImpactControl:
    """
    원리:
      층간소음의 특성: 충격(발걸음, 물건 낙하) 후 잔향(ringdown)이 길게 남음
      두 단계로 처리:
        1. Impact Detection: 에너지 급상승 감지 (충격 순간)
        2. Ringdown Modeling: 지수 감쇠 모델로 잔향 예측 후 상쇄

      잔향 모델: r[n] = A * exp(-decay * n) * cos(2π*f0*n)
        A     = 충격 크기
        decay = 감쇠 계수 (환경에 따라 적응)
        f0    = 지배 주파수 (LPC로 추정)

    장점: 층간소음 특화, 충격 후 잔향 제거에 탁월
    단점: 비충격성 소음에는 효과 낮음
    최적 소음: 발걸음, 의자 끌기, 물건 낙하
    """

    IMPACT_THRESHOLD  = 0.15   # 충격 감지 에너지 임계값
    DECAY_INIT        = 0.005  # 초기 감쇠 계수
    DECAY_ADAPT_RATE  = 0.05   # 감쇠 계수 적응 속도
    LPC_ORDER         = 8      # LPC 분석 차수

    def __init__(self):
        self.in_ringdown   = False
        self.ringdown_buf  = np.zeros(CHUNK_SIZE * 4, dtype=np.float64)
        self.impact_amp    = 0.0
        self.decay         = self.DECAY_INIT
        self.f0            = 60.0   # 초기 지배 주파수 (Hz)
        self.ringdown_pos  = 0
        self._energy_hist  = deque(maxlen=10)
        self._lpc_coeffs   = np.zeros(self.LPC_ORDER, dtype=np.float64)
        self._prev_chunk   = np.zeros(CHUNK_SIZE, dtype=np.float64)
        self.name          = "RingdownImpactControl"

    def process(self, x: np.ndarray) -> Tuple[np.ndarray, float]:
        energy = float(np.mean(x ** 2))
        self._energy_hist.append(energy)

        anti = np.zeros(len(x), dtype=np.float64)

        # ① 충격 감지
        if self._detect_impact(energy):
            self.in_ringdown  = True
            self.impact_amp   = float(np.max(np.abs(x)))
            self.ringdown_pos = 0
            self._estimate_decay(x)
            self._estimate_f0(x)

        # ② 잔향 상쇄음 생성
        if self.in_ringdown:
            anti = self._generate_ringdown_anti(len(x))
            self.ringdown_pos += len(x)

            # 잔향 에너지가 임계값 아래로 떨어지면 종료
            expected_amp = self.impact_amp * np.exp(-self.decay * self.ringdown_pos)
            if expected_amp < self.IMPACT_THRESHOLD * 0.1:
                self.in_ringdown = False

            # 감쇠 계수 온라인 적응
            self._adapt_decay(x, anti)

        self._prev_chunk = x.copy()
        rate = cancellation_rate(x, anti)
        return anti, rate

    def _detect_impact(self, energy: float) -> bool:
        """에너지 급상승 감지 (현재 에너지 > 평균의 3배)"""
        if len(self._energy_hist) < 3:
            return False
        avg_prev = float(np.mean(list(self._energy_hist)[:-1]))
        return energy > max(self.IMPACT_THRESHOLD, avg_prev * 3.0)

    def _estimate_decay(self, x: np.ndarray):
        """
        신호 포락선의 로그 기울기로 감쇠 계수 추정
        env[n] = A * exp(-decay * n)  →  log(env) = log(A) - decay*n
        """
        env = np.abs(x)
        env = np.where(env < 1e-8, 1e-8, env)
        log_env = np.log(env)
        n       = np.arange(len(log_env), dtype=np.float64)
        # 최소자승법으로 기울기 추정
        slope   = np.polyfit(n, log_env, 1)[0]
        new_decay = max(0.001, -slope)
        # 지수 이동 평균으로 부드럽게 갱신
        self.decay = (1 - self.DECAY_ADAPT_RATE) * self.decay + \
                      self.DECAY_ADAPT_RATE * new_decay

    def _estimate_f0(self, x: np.ndarray):
        """
        LPC(선형 예측 코딩)로 지배 주파수 추정
        """
        if np.std(x) < 1e-8:
            return
        # 자기상관 기반 LPC
        r = np.correlate(x, x, mode='full')
        r = r[len(r)//2:]
        r = r[:self.LPC_ORDER + 1]
        if r[0] < 1e-10:
            return
        # Levinson-Durbin
        a = np.zeros(self.LPC_ORDER, dtype=np.float64)
        e = r[0]
        for i in range(self.LPC_ORDER):
            if e < 1e-10:
                break
            k = -sum(a[j] * r[i - j] for j in range(i)) / e
            k = np.clip(k, -0.99, 0.99)
            a_new      = a[:i] + k * a[:i][::-1]
            a[:i]      = a_new
            a[i]       = k
            e          *= (1 - k ** 2)
        self._lpc_coeffs = a

        # LPC 극점에서 지배 주파수 추출
        roots = np.roots(np.concatenate([[1], self._lpc_coeffs]))
        roots = roots[np.abs(roots) < 1.0]  # 안정 극점만
        angles = np.angle(roots)
        freqs  = np.abs(angles) * SAMPLE_RATE / (2 * np.pi)
        freqs  = freqs[(freqs > 20) & (freqs < SAMPLE_RATE / 2 - 20)]
        if len(freqs) > 0:
            self.f0 = float(np.min(freqs))  # 가장 낮은 지배 주파수

    def _generate_ringdown_anti(self, length: int) -> np.ndarray:
        """
        지수 감쇠 정현파로 잔향 상쇄음 생성
        anti[n] = -A * exp(-decay*(pos+n)) * cos(2π*f0*(pos+n)/sr)
        """
        n    = np.arange(self.ringdown_pos, self.ringdown_pos + length, dtype=np.float64)
        env  = self.impact_amp * np.exp(-self.decay * n)
        wave = np.cos(2 * np.pi * self.f0 * n / SAMPLE_RATE)
        return -env * wave

    def _adapt_decay(self, x: np.ndarray, anti: np.ndarray):
        """잔류 에너지 기반 감쇠 계수 미세 조정"""
        residual = float(np.mean((x + anti) ** 2))
        original = float(np.mean(x ** 2))
        if original < 1e-10:
            return
        # 잔류가 크면 감쇠 빠르게, 작으면 느리게
        ratio = residual / original
        if ratio > 0.5:
            self.decay *= 1.02   # 감쇠 가속
        elif ratio < 0.1:
            self.decay *= 0.98   # 감쇠 완화


# ══════════════════════════════════════════
# 경쟁 선택 엔진
# ══════════════════════════════════════════
class CompetitiveANCEngine:
    """
    3가지 방식을 매 청크 동시 연산 후 최고 상쇄율 방식 선택

    선택 안정화:
      - 단순 최대값 선택이 아닌 지수 이동 평균(EMA)으로 안정화
      - 갑작스러운 방식 전환으로 인한 클릭음 방지를 위해
        방식 전환 시 크로스페이드 적용
    """

    EMA_ALPHA      = 0.2   # 상쇄율 EMA 계수 (낮을수록 안정적)
    CROSSFADE_LEN  = 32    # 방식 전환 시 크로스페이드 샘플 수

    def __init__(self):
        self.engines = [
            FixedGainDelay(),
            FxNLMS(),
            RingdownImpactControl(),
        ]
        self.n = len(self.engines)

        # 각 방식의 EMA 상쇄율
        self.ema_rates  = np.zeros(self.n, dtype=np.float64)
        # 현재 선택된 방식 인덱스
        self.active_idx = 0
        # 직전 청크의 상쇄음 (크로스페이드용)
        self._prev_anti = np.zeros(CHUNK_SIZE, dtype=np.float64)
        self._prev_idx  = 0

        # 통계
        self.win_counts = np.zeros(self.n, dtype=np.int64)
        self.chunk_count = 0

    def process(self, x: np.ndarray) -> Tuple[np.ndarray, int, np.ndarray]:
        """
        반환: (상쇄음, 선택된 방식 인덱스, 각 방식 상쇄율 배열)
        """
        results = []
        rates   = np.zeros(self.n, dtype=np.float64)

        # ① 3가지 방식 동시 연산
        for i, engine in enumerate(self.engines):
            anti, rate = engine.process(x)
            results.append(anti)
            rates[i] = rate

        # ② EMA로 상쇄율 안정화
        self.ema_rates = (1 - self.EMA_ALPHA) * self.ema_rates + \
                          self.EMA_ALPHA * rates

        # ③ 최고 EMA 상쇄율 방식 선택
        new_idx = int(np.argmax(self.ema_rates))

        # ④ 방식 전환 시 크로스페이드 (클릭음 방지)
        best_anti = results[new_idx]
        if new_idx != self.active_idx:
            best_anti = self._crossfade(
                self._prev_anti, results[new_idx]
            )
            self.active_idx = new_idx

        # ⑤ 통계 갱신
        self.win_counts[new_idx] += 1
        self.chunk_count += 1
        self._prev_anti = best_anti.copy()
        self._prev_idx  = new_idx

        return best_anti, new_idx, rates

    def _crossfade(self, old: np.ndarray, new: np.ndarray) -> np.ndarray:
        """두 상쇄음 신호를 부드럽게 전환 (클릭음 방지)"""
        length = min(self.CROSSFADE_LEN, len(old), len(new))
        fade   = np.linspace(0.0, 1.0, length, dtype=np.float64)
        out    = new.copy()
        out[:length] = old[:length] * (1 - fade) + new[:length] * fade
        return out

    def stats_str(self) -> str:
        names = [e.name for e in self.engines]
        parts = []
        for i, name in enumerate(names):
            pct = self.win_counts[i] / max(1, self.chunk_count) * 100
            parts.append(f"{name}:{pct:.0f}%({self.ema_rates[i]*100:.1f}%)")
        return " | ".join(parts)


# ──────────────────────────────────────────
# ESP32 연결 핸들러
# ──────────────────────────────────────────
class ESP32Handler:

    def __init__(self, engine: CompetitiveANCEngine, stats: dict):
        self.engine  = engine
        self.stats   = stats
        self._send_q: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def run(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        log.info(f"ESP32 연결됨: {addr}")
        try:
            await asyncio.gather(
                self._recv_loop(reader),
                self._send_loop(writer),
            )
        except Exception as e:
            log.warning(f"연결 종료 ({addr}): {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info(f"ESP32 연결 해제: {addr}")

    async def _recv_loop(self, reader: asyncio.StreamReader):
        names = ["FixedGainDelay", "FxNLMS", "RingdownImpact"]

        while True:
            raw = await asyncio.wait_for(
                reader.readexactly(CHUNK_BYTES), timeout=2.0
            )

            # 16bit PCM → float64 [-1, 1]
            pcm  = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0

            # 경쟁 연산
            anti, winner_idx, rates = self.engine.process(pcm)

            # float64 → int16 PCM
            anti_pcm = np.clip(anti * 32767.0, -32768, 32767).astype(np.int16)

            try:
                self._send_q.put_nowait(anti_pcm.tobytes())
            except asyncio.QueueFull:
                try:
                    self._send_q.get_nowait()
                    self._send_q.put_nowait(anti_pcm.tobytes())
                except Exception:
                    pass

            # 통계
            self.stats["chunks"]  += 1
            self.stats["winner"]   = names[winner_idx]
            self.stats["rates"]    = {
                names[i]: float(rates[i]) * 100 for i in range(3)
            }

            if self.stats["chunks"] % 100 == 0:
                log.info(
                    f"청크 {self.stats['chunks']:,} | "
                    f"승자: {self.stats['winner']} | "
                    f"{self.engine.stats_str()}"
                )

    async def _send_loop(self, writer: asyncio.StreamWriter):
        while True:
            data = await self._send_q.get()
            writer.write(data)
            await writer.drain()


# ──────────────────────────────────────────
# ANC 서버
# ──────────────────────────────────────────
class ANCServer:

    def __init__(self):
        self.engine = CompetitiveANCEngine()
        self.stats  = {"chunks": 0, "winner": "-", "rates": {}}

    async def _on_connect(self, reader, writer):
        handler = ESP32Handler(self.engine, self.stats)
        await handler.run(reader, writer)

    async def run(self):
        server = await asyncio.start_server(
            self._on_connect, HOST, PORT,
            limit=CHUNK_BYTES * 16,
        )
        log.info("=" * 58)
        log.info("   ANC 서버 - 3방식 경쟁 선택 엔진")
        log.info(f"   포트       : {PORT}")
        log.info(f"   샘플레이트 : {SAMPLE_RATE} Hz")
        log.info(f"   청크       : {CHUNK_SIZE}샘플 = {CHUNK_SIZE/SAMPLE_RATE*1000:.0f}ms")
        log.info("   방식       : FixedGainDelay / FxNLMS / RingdownImpact")
        log.info("   ESP32 연결 대기 중...")
        log.info("=" * 58)
        async with server:
            await server.serve_forever()


# ──────────────────────────────────────────
# 엔트리포인트
# ──────────────────────────────────────────
def main():
    server = ANCServer()

    def _sig(sig, frame):
        log.info("종료")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
