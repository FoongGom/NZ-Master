import sounddevice as sd
import numpy as np
import time
import tempfile
import os
import wave
from noise_zero_mariadb_logger import log_noise_detection

# ================== Settings ==================
FS = 48000
CHUNK_DURATION = 10
print("=" * 60)
print("NoiseZero - Real-time ANC + Noise Classification System")
print("=" * 60)
print(f"Recording {CHUNK_DURATION}-second chunks at {FS}Hz")
print("Press Ctrl + C to stop\n")

try:
    while True:
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] Starting {CHUNK_DURATION}-second recording...")
       
        # Record audio
        recording = sd.rec(
            int(CHUNK_DURATION * FS),
            samplerate=FS,
            channels=1,
            dtype='float32',
            blocking=True
        )
       
        # Save to temp wav
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
       
        with wave.open(tmp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(FS)
            int16_data = np.int16(recording * 32767)
            wf.writeframes(int16_data.tobytes())
       
        print(f"[{timestamp}] Recording complete -> Starting classification")
       
        # Classify and log
        try:
            result = log_noise_detection(
                location="1211 Classroom",   # 필요하면 위치 이름 바꾸세요
                query_file=tmp_path,
                min_similarity=0.0
            )
            if isinstance(result, dict):
                similarity = result.get('similarity', 0)
                label = result.get('label', 'Unknown')
                print(f"Classified: {label} (Similarity: {similarity:.3f})")
            else:
                print("Classification returned unexpected format")
        except Exception as e:
            print(f"Classification error: {e}")
       
        # Cleanup temp file
        try:
            os.remove(tmp_path)
        except:
            pass
       
        time.sleep(2)   # Interval between recordings
except KeyboardInterrupt:
    print("\nSystem stopped by user")
except Exception as e:
    print(f"System error: {e}")
