import pymysql
import pickle
import os
import librosa
import numpy as np
from datetime import datetime

# MariaDB 연결 정보 (Pi 환경에 맞게 수정)
DB_CONFIG = {
    'host': '172.16.113.66',
    'user': 'LSM',           # 너가 사용하는 사용자
    'password': 'your_password',   # ← 실제 비밀번호로 변경
    'database': 'noisezero'
}

def init_mariadb_table():
    """MariaDB 테이블 초기화"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS noise_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME,
            location VARCHAR(100),
            noise_type VARCHAR(100),
            similarity FLOAT,
            file_path VARCHAR(255)
        )
    """)
    conn.commit()
    conn.close()
    print("✅ MariaDB 테이블 준비 완료")

def extract_audio_features(audio_path):
    """MFCC 특징 추출"""
    try:
        y, sr = librosa.load(audio_path, sr=22050)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        delta = librosa.feature.delta(mfcc)
        delta2 = librosa.feature.delta(mfcc, order=2)
        features = np.concatenate([mfcc.mean(axis=1), 
                                   delta.mean(axis=1), 
                                   delta2.mean(axis=1)])
        return features
    except Exception as e:
        print(f"특징 추출 실패: {e}")
        return None

def build_sound_database(reference_folder):
    """참조 DB 구축"""
    database = {}
    for root, dirs, files in os.walk(reference_folder):
        for file in files:
            if file.endswith('.wav'):
                path = os.path.join(root, file)
                label = os.path.basename(root)
                features = extract_audio_features(path)
                if features is not None:
                    database[label] = features
                    print(f"추가: {label} - {file}")
    with open("sound_database.pkl", "wb") as f:
        pickle.dump(database, f)
    print(f"✅ DB 구축 완료: {len(database)} 종류")

def find_most_similar_sound(query_features, database):
    """가장 유사한 소음 찾기"""
    if not database:
        return None, 0.0
    
    best_label = None
    best_sim = -1.0
    
    for label, features in database.items():
        similarity = np.dot(query_features, features) / (np.linalg.norm(query_features) * np.linalg.norm(features))
        if similarity > best_sim:
            best_sim = similarity
            best_label = label
    
    return best_label, best_sim

def log_noise_detection(location, query_file, min_similarity=0.0):
    """녹음 파일 분석 후 DB에 기록 (항상 기록)"""
    features = extract_audio_features(query_file)
    if features is None:
        return None
    
    with open("sound_database.pkl", "rb") as f:
        database = pickle.load(f)
    
    label, similarity = find_most_similar_sound(features, database)
    
    if label is None:
        return None
    
    # 항상 기록 (min_similarity 무시)
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO noise_logs (timestamp, location, noise_type, similarity, file_path)
        VALUES (%s, %s, %s, %s, %s)
    """, (datetime.now(), location, label, float(similarity), query_file))
    conn.commit()
    conn.close()
    
    return {"label": label, "similarity": similarity}

def analyze_noise_patterns():
    """저장된 패턴 분석"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT noise_type, COUNT(*) as count FROM noise_logs GROUP BY noise_type ORDER BY count DESC")
    results = cursor.fetchall()
    conn.close()
    
    print("\n=== 소음 발생 패턴 분석 ===")
    for row in results:
        print(f"{row[0]}: {row[1]}회")
