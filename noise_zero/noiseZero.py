import sounddevice as sd
import numpy as np
import time
import tempfile
import os
import wave
from noise_zero_mariadb_logger import log_noise_detection

# ================== 설정 ==================
FS = 48000
CHUNK_DURATION = 10
BLOCK_SIZE = 4096
GAIN = 12.0                    # 적당한 gain

print("라즈베리파이 통합 ANC + 소음 분류 시스템")
print("Ctrl + C로 종료")

try:
    while True:
        print(f"\n[{time.strftime('%H:%M:%S')}] 10초 녹음 + 처리 중...")
        
        # 10초 녹음
        recording = sd.rec(int(CHUNK_DURATION * FS), 
                           samplerate=FS, 
                           channels=1, 
                           dtype='float32', 
                           blocking=True)
        
        # 임시 WAV 저장
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
        
        with wave.open(tmp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(FS)
            int16_data = np.int16(recording * 32767)
            wf.writeframes(int16_data.tobytes())
        
        # 간단 ANC 처리 (기록용)
        # (여기에 delay나 gain 적용 가능)
        
        # 모델 분류 + MariaDB 로그
        try:
            result = log_noise_detection(
                location="1211 강의실",
                query_file=tmp_path,
                min_similarity=0.60
            )
            if result:
                print(f"✅ 분류: {result.get('label', 'Unknown')} (유사도: {result.get('similarity', 0):.2f})")
            else:
                print("⚠️ 유사 소음 없음")
        except Exception as e:
            print(f"분류 에러: {e}")
        
        # 임시 파일 삭제
        os.remove(tmp_path)
        
        time.sleep(2)

except KeyboardInterrupt:
    print("\n시스템 종료")
except Exception as e:
    print(f"전체 에러: {e}")
