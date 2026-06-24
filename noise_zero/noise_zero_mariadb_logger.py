import pymysql
import pickle
import os
import librosa
import numpy as np
from datetime import datetime

DB_CONFIG = {
    'host': '172.16.113.66',
    'user': 'LSM',
    'password': 'password',      
    'database': 'noise_monitoring'
}

def init_mariadb_table():
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
    print("MariaDB table OK")

def extract_audio_features(audio_path):
    try:
        y, sr = librosa.load(audio_path, sr=22050)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        delta = librosa.feature.delta(mfcc)
        delta2 = librosa.feature.delta(mfcc, order=2)
        features = np.concatenate([mfcc.mean(axis=1), delta.mean(axis=1), delta2.mean(axis=1)])
        print(f"  Features shape: {features.shape} for {os.path.basename(audio_path)}")
        return features
    except Exception as e:
        print(f"  Feature extraction failed: {e}")
        return None

def build_sound_database(reference_folder):
    database = []   # list 형태 유지
    count = 0
    print(f"Building database from: {reference_folder}")
    for root, dirs, files in os.walk(reference_folder):
        label = os.path.basename(root)
        if not label or label.startswith('.') or label == "sound_sample":
            continue
        print(f"Folder: {label}")
        for file in files:
            if file.endswith('.wav'):
                path = os.path.join(root, file)
                features = extract_audio_features(path)
                if features is not None:
                    database.append((label, features))
                    count += 1
                    print(f"  Added: {label} - {file}")
    try:
        with open("sound_database.pkl", "wb") as f:
            pickle.dump(database, f)
        size = os.path.getsize("sound_database.pkl")
        print(f"LIST Database saved: {len(database)} samples, size={size} bytes")
    except Exception as e:
        print(f"Save failed: {e}")
    return database

def find_most_similar_sound(query_features, database):
    print(f"Matching against {len(database)} samples...")
    if not database or query_features is None:
        print("  Database empty or no features")
        return "Unknown", 0.0
   
    best_label = "Unknown"
    best_sim = -1.0
    for item in database:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            label = item[0]
            features = item[1]
            try:
                sim = np.dot(query_features, features) / (
                    np.linalg.norm(query_features) * np.linalg.norm(features) + 1e-8
                )
                if sim > best_sim:
                    best_sim = sim
                    best_label = label
            except Exception as e:
                print(f"  Similarity calc error for {label}: {e}")
    print(f"Best match: {best_label} (sim={best_sim:.3f})")
    return best_label, best_sim

def log_noise_detection(location, query_file, min_similarity=0.0):
    print(f"Analyzing file: {query_file}")
    features = extract_audio_features(query_file)
    if features is None:
        label = "Unknown"
        similarity = 0.0
    else:
        try:
            with open("sound_database.pkl", "rb") as f:
                database = pickle.load(f)
            label, similarity = find_most_similar_sound(features, database)
        except Exception as e:
            print(f"CRITICAL DB load error: {e}")
            label = "Unknown"
            similarity = 0.0
   
    if similarity < min_similarity:
        label = "Unknown"
   
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO noise_logs (timestamp, location, noise_type, similarity, file_path)
            VALUES (%s, %s, %s, %s, %s)
        """, (datetime.now(), location, label, float(similarity), query_file))
        conn.commit()
        conn.close()
        print(f"LOGGED -> {label} (sim: {similarity:.3f})")
    except Exception as e:
        print(f"DB insert failed: {e}")
   
    return {"label": label, "similarity": similarity}
