import pymysql
import pickle
import os
import librosa
import numpy as np
from datetime import datetime

# MariaDB connection info (for Pi)
DB_CONFIG = {
    'host': '172.16.113.66',
    'user': 'LSM',
    'password': 'password',     
    'database': 'noise_monitoring'
}

def init_mariadb_table():
    """Initialize MariaDB table"""
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
    print("MariaDB table initialized successfully")

def extract_audio_features(audio_path):
    """Extract MFCC + delta + delta2 features"""
    try:
        y, sr = librosa.load(audio_path, sr=22050)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        delta = librosa.feature.delta(mfcc)
        delta2 = librosa.feature.delta(mfcc, order=2)
        features = np.concatenate([
            mfcc.mean(axis=1),
            delta.mean(axis=1),
            delta2.mean(axis=1)
        ])
        return features
    except Exception as e:
        print(f"Feature extraction failed: {e}")
        return None

def build_sound_database(reference_folder):
    """Build reference database from sound samples"""
    database = {}
    count = 0
    print(f"Scanning folder: {reference_folder}")
    for root, dirs, files in os.walk(reference_folder):
        label = os.path.basename(root)
        if label == "sound_sample" or not label or label.startswith('.'):
            continue
        print(f"Processing folder: {label} ({len(files)} files)")
        features_list = []
        for file in files:
            if file.endswith('.wav'):
                path = os.path.join(root, file)
                print(f"  Processing file: {file}")
                features = extract_audio_features(path)
                if features is not None:
                    features_list.append(features)
                    count += 1
        if features_list:
            # Average if multiple samples per label
            database[label] = np.mean(features_list, axis=0)
            print(f" Added label: {label} (avg from {len(features_list)} files)")
    with open("sound_database.pkl", "wb") as f:
        pickle.dump(database, f)
    print(f"Database built successfully: {len(database)} types, {count} files")
    return database

def find_most_similar_sound(query_features, database):
    """Find most similar sound using cosine similarity (fixed for dict database)"""
    if not database or query_features is None:
        return "Unknown", 0.0
   
    best_label = "Unknown"
    best_sim = -1.0
   
    for label, features in database.items():
        if features is None:
            continue
        try:
            # Cosine similarity
            similarity = np.dot(query_features, features) / (
                np.linalg.norm(query_features) * np.linalg.norm(features) + 1e-8
            )
            if similarity > best_sim:
                best_sim = similarity
                best_label = label
        except Exception:
            continue
   
    return best_label, best_sim

def log_noise_detection(location, query_file, min_similarity=0.0):
    """Main classification + logging function"""
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
            print(f"Database load or matching error: {e}")
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
        print(f"DB log saved: {label} (sim: {similarity:.3f})")
    except Exception as e:
        print(f"DB save failed: {e}")
   
    return {"label": label, "similarity": similarity}

def analyze_noise_patterns():
    """Analyze saved noise patterns"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT noise_type, COUNT(*) as count FROM noise_logs GROUP BY noise_type ORDER BY count DESC")
        results = cursor.fetchall()
        conn.close()
       
        print("\n=== Noise Occurrence Patterns ===")
        for row in results:
            print(f"{row[0]}: {row[1]} times")
    except Exception as e:
        print(f"Pattern analysis failed: {e}")

# For testing
if __name__ == "__main__":
    init_mariadb_table()
