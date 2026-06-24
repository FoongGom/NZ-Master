"""
NoiseZero - 층간소음 모니터링 시스템 (Raspberry Pi 초기화용)
- 참조 DB 구축
- 테이블 초기화
- 분석 실행
"""

from noise_zero_mariadb_logger import (
    init_mariadb_table,
    build_sound_database,
    analyze_noise_patterns
)
import os

if __name__ == "__main__":
    print("=" * 80)
    print("🔊 NoiseZero - 초기화 및 DB 구축 (Pi 버전)")
    print("=" * 80)
    
    # 1. MariaDB 테이블 초기화
    init_mariadb_table()
    
    # ====================== 설정 영역 ======================
    REFERENCE_FOLDER = "/home/noisezero/sound_sample"   # Pi에 맞는 경로
    
    LOCATION = "1211 강의실"
    # =====================================================
    
    # 2. 참조 소음 DB 구축
    print("\n[1단계] 참조 소음 DB 구축")
    if not os.path.exists("sound_database.pkl"):
        build_sound_database(REFERENCE_FOLDER)
    else:
        print("✅ sound_database.pkl이 이미 존재합니다.")
        print("   (새로운 소리를 추가했다면 sound_database.pkl을 삭제하고 다시 실행하세요)")
    
    # 3. 현재까지 저장된 데이터 분석
    print("\n[2단계] 저장된 소음 패턴 분석")
    analyze_noise_patterns()
    
    print("\n✅ 초기화 완료! 이제 anc_with_classification.py를 실행하세요.")
