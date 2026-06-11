# noise_zero_model.py
"""
메인 실행 파일
- 참조 DB 구축
- 테스트 실행
- 분석 실행
"""

from noise_zero_mariadb_logger import (
    init_mariadb_table,
    build_sound_database,
    log_noise_detection,
    analyze_noise_patterns,
    find_most_similar_sound
)
import os


if __name__ == "__main__":
    print("=" * 80)
    print("🔊 NoiseZero - 층간소음 모니터링 시스템")
    print("=" * 80)
    
    # 1. MariaDB 테이블 초기화 (처음 1회)
    init_mariadb_table()
    
    # ====================== 설정 영역 ======================
    # ★★★ 여기만 수정하면 됩니다 ★★★
    REFERENCE_FOLDER = r"D:\NoiseZero\sound_sample\adult_foot_sound"  # 네 샘플 폴더
    
    # 테스트할 파일 예시 (나중에 실제 녹음 파일로 바꾸세요)
    TEST_FILE = r"D:\NoiseZero\sound_sample\adult_foot_sound\N-10_220831_A_1_a_00973.wav"   # ← 실제 테스트 파일 경로
    
    LOCATION = "1211 강의실"                        # 위치 정보
    # =====================================================
    
    # 2. 참조 소음 DB 구축 (처음 한 번만 실행)
    print("\n[1단계] 참조 소음 DB 구축")
    if not os.path.exists("sound_database.pkl"):
        build_sound_database(REFERENCE_FOLDER)
    else:
        print("✅ sound_database.pkl이 이미 존재합니다.")
    
    # 3. 테스트 실행
    print("\n[2단계] 테스트 소음 분석 및 저장")
    if os.path.exists(TEST_FILE):
        log_noise_detection(
            location=LOCATION,
            query_file=TEST_FILE,
            min_similarity=0.65
        )
    else:
        print(f"⚠️ 테스트 파일이 없습니다: {TEST_FILE}")
        print("   실제 wav 파일 경로를 TEST_FILE에 넣고 다시 실행하세요.")
    
    # 4. 현재까지 저장된 데이터 분석
    print("\n[3단계] 저장된 소음 패턴 분석")
    analyze_noise_patterns()