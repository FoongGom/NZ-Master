# noise_zero_mariadb_logger.py
"""
층간소음 모니터링 시스템 - MariaDB 연동 버전
주요 기능:
  - 오디오 특징 추출 (MFCC)
  - 참조 소음 DB 관리 (sound_database.pkl)
  - 새 소음 분석 및 가장 유사한 소음 찾기
  - MariaDB에 발생 로그 저장
"""

import os
import pickle
import pymysql
import numpy as np
import librosa
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


# ====================== MariaDB 설정 ======================
DB_CONFIG = {
    'host': '172.16.113.66',           # ← 네 MariaDB 서버 IP (필요시 변경)
    'user': 'LSM',                     # ← 네가 사용하는 사용자명
    'password': 'password',  # ← 반드시 수정!
    'database': 'noise_monitoring',    # 데이터베이스 이름
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def get_connection():
    try:
        conn = pymysql.connect(**DB_CONFIG)
        print("✅ MariaDB 연결 성공!")
        return conn
    except pymysql.Error as e:
        print(f"❌ MariaDB 연결 실패: {e}")
        if e.args[0] == 1045:
            print("   → 비밀번호가 틀렸거나, 해당 IP에서 접속이 허용되지 않았습니다.")
        elif e.args[0] == 1044 or e.args[0] == 1049:
            print("   → 데이터베이스 'noise_monitoring'이 존재하지 않습니다.")
        return None


def init_mariadb_table():
    """noise_detections 테이블 생성 (처음 1회 실행)"""
    conn = get_connection()
    if not conn:
        return
    
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS noise_detections (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                location VARCHAR(100) NOT NULL,
                noise_type VARCHAR(50) NOT NULL,
                similarity FLOAT,
                source_file VARCHAR(255),
                notes TEXT,
                INDEX idx_location (location),
                INDEX idx_timestamp (timestamp),
                INDEX idx_noise_type (noise_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
    conn.commit()
    conn.close()
    print("✅ MariaDB 테이블 초기화 완료 (noise_detections)")


# ====================== 오디오 특징 추출 ======================
def extract_audio_features(file_path, sr=22050, n_mfcc=13):
    """
    wav 파일에서 MFCC 특징 벡터 추출
    - sr: 샘플링 레이트 (22050Hz 추천)
    - n_mfcc: MFCC 계수 개수 (13~20 사이가 적당)
    """
    try:
        # 1. 오디오 로드 (모노)
        y, sr = librosa.load(file_path, sr=sr, mono=True)
        
        # 2. 무음 구간 제거 (배경 잡음 줄이기)
        y, _ = librosa.effects.trim(y, top_db=20)
        
        # 3. MFCC 추출
        mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
        
        # 4. 시간 축으로 평균과 표준편차 계산 → 고정 길이 특징 벡터
        mfcc_mean = np.mean(mfccs, axis=1)
        mfcc_std = np.std(mfccs, axis=1)
        
        # mean + std 결합 (더 풍부한 특징)
        features = np.concatenate([mfcc_mean, mfcc_std])
        
        return features
        
    except Exception as e:
        print(f"❌ 특징 추출 실패 ({file_path}): {e}")
        return None


# ====================== 참조 소음 DB 관리 ======================
def build_sound_database(reference_dir, db_save_path="sound_database.pkl"):
    """
    sound_sample 폴더 안의 모든 wav 파일을 특징 추출해서 pkl 파일로 저장
    """
    database = []
    supported_ext = ('.wav', '.mp3', '.flac', '.ogg')
    
    print(f"📁 참조 소음 DB 구축 시작: {reference_dir}\n")
    
    for root, dirs, files in os.walk(reference_dir):
        for filename in files:
            if filename.lower().endswith(supported_ext):
                file_path = os.path.join(root, filename)
                label = os.path.basename(root)  # 폴더 이름 = 소음 종류
                
                features = extract_audio_features(file_path)
                
                if features is not None:
                    database.append({
                        'filename': filename,
                        'filepath': file_path,
                        'label': label,
                        'features': features
                    })
                    print(f"  ✓ 처리 완료: {filename}  →  [{label}]")
    
    # pickle로 저장
    with open(db_save_path, 'wb') as f:
        pickle.dump(database, f)
    
    print(f"\n🎉 참조 DB 구축 완료! 총 {len(database)}개 샘플 저장됨")
    print(f"   파일 위치: {os.path.abspath(db_save_path)}\n")
    return database


def load_sound_database(db_path="sound_database.pkl"):
    """저장된 참조 DB 불러오기"""
    try:
        with open(db_path, 'rb') as f:
            return pickle.load(f)
    except FileNotFoundError:
        print(f"❌ {db_path} 파일이 없습니다. 먼저 build_sound_database()를 실행하세요.")
        return None


# ====================== 소음 분석 및 로그 저장 ======================
def find_most_similar_sound(query_file, ref_db_path="sound_database.pkl", top_k=3):
    """
    새로운 소음 파일과 가장 유사한 참조 샘플 찾기
    """
    database = load_sound_database(ref_db_path)
    if not database:
        return []
    
    query_features = extract_audio_features(query_file)
    if query_features is None:
        return []
    
    query_vec = query_features.reshape(1, -1)
    results = []
    
    for item in database:
        ref_vec = item['features'].reshape(1, -1)
        similarity = cosine_similarity(query_vec, ref_vec)[0][0]
        
        results.append({
            'similarity': round(similarity, 4),
            'label': item['label'],
            'filename': item['filename'],
            'filepath': item['filepath']
        })
    
    results.sort(key=lambda x: x['similarity'], reverse=True)
    return results[:top_k]


def log_noise_detection(location, query_file, min_similarity=0.65, 
                       ref_db_path="sound_database.pkl", notes=""):
    """
    소음 분석 후 MariaDB에 기록
    """
    results = find_most_similar_sound(query_file, ref_db_path)
    if not results:
        print("❌ 분석 실패")
        return None
    
    top = results[0]
    
    if top['similarity'] < min_similarity:
        print(f"⚠️ 유사도 부족 ({top['similarity']}) → 로그 저장 안 함")
        return None
    
    conn = get_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO noise_detections 
                (location, noise_type, similarity, source_file, notes)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                location,
                top['label'],
                top['similarity'],
                os.path.basename(query_file),
                notes or f"가장 유사한 샘플: {top['filename']}"
            ))
        conn.commit()
        print(f"✅ 로그 저장 완료 → [{location}] {top['label']} (유사도: {top['similarity']})")
        return top
    finally:
        conn.close()


# ====================== 분석 함수 ======================
def analyze_noise_patterns():
    """저장된 로그를 pandas로 분석"""
    conn = get_connection()
    if not conn:
        return
    
    df = pd.read_sql("SELECT * FROM noise_detections", conn)
    conn.close()
    
    if df.empty:
        print("📭 저장된 로그가 없습니다.")
        return df
    
    print("\n" + "="*70)
    print("📊 위치별 소음 발생 통계")
    print("="*70)
    print(pd.crosstab(df['location'], df['noise_type']))
    
    print("\n📊 위치별 가장 많이 발생한 소음")
    for loc in df['location'].unique():
        sub = df[df['location'] == loc]
        print(f"\n[{loc}]")
        print(sub['noise_type'].value_counts().head(5))
    
    return df