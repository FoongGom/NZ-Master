/*
  ESP32_INMP441_SERIAL_STREAM.ino

  부품 구조:
  INMP441 마이크 -> ESP32 -> USB Serial -> Raspberry Pi -> PAM8403 -> Speaker

  ESP32 역할:
  - INMP441 I2S 마이크에서 소리 샘플을 읽음
  - 너무 빠른 원본 오디오 전체를 보내기보다, 1차 테스트용으로 다운샘플링해서 Raspberry Pi에 전달
  - Raspberry Pi가 이 값을 받아 반대 위상 출력 신호를 만든다

  연결 예시:
  INMP441 VDD  -> ESP32 3.3V
  INMP441 GND  -> ESP32 GND
  INMP441 SCK  -> ESP32 GPIO14
  INMP441 WS   -> ESP32 GPIO15
  INMP441 SD   -> ESP32 GPIO32
  INMP441 L/R  -> GND

  주의:
  - 완전 상쇄용 고성능 ANC가 아니라, 저주파 소리 감지 + 일부 저감 가능성 확인용 프로토타입 구조입니다.
  - ESP32 -> Raspberry Pi 통신 지연 때문에 발소리/낙하음 같은 순간 충격음 제어는 어렵습니다.
  - 세탁기 웅웅거림, 사람 말소리 낮은 성분, 지속 저주파 소리 테스트에 더 적합합니다.
*/

#include <Arduino.h>
#include "driver/i2s.h"

// =========================================================
// 1. INMP441 I2S 핀 설정
// =========================================================

#define I2S_MIC_PORT I2S_NUM_0
#define I2S_MIC_SCK  14
#define I2S_MIC_WS   15
#define I2S_MIC_SD   32

// =========================================================
// 2. 샘플링 설정
// =========================================================

// INMP441에서 읽는 내부 샘플링 주파수
const int MIC_SAMPLE_RATE = 8000;

// Raspberry Pi로 보내는 샘플링 주파수
// Serial 통신 부담을 줄이기 위해 1000Hz로 다운샘플링
const int SEND_SAMPLE_RATE = 1000;

// 8000Hz에서 1000Hz로 줄이기 위한 비율
const int DECIMATION = MIC_SAMPLE_RATE / SEND_SAMPLE_RATE;

// Serial 통신 속도
// Raspberry Pi Python 코드와 같은 값이어야 함
const int SERIAL_BAUD = 921600;

// INMP441 원본 값을 적당히 줄이기 위한 shift
// 값이 너무 작게 나오면 14 -> 12
// 값이 너무 크게 나오면 14 -> 16
const int MIC_SHIFT = 14;

// =========================================================
// 3. 내부 변수
// =========================================================

int decimationCount = 0;
int32_t decimationSum = 0;

unsigned long lastInfoPrint = 0;
long sentCount = 0;

// =========================================================
// 4. I2S 초기화
// =========================================================

void setupI2SMic() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = MIC_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 64,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_MIC_SCK,
    .ws_io_num = I2S_MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_MIC_SD
  };

  esp_err_t err;

  err = i2s_driver_install(I2S_MIC_PORT, &i2s_config, 0, NULL);
  if (err != ESP_OK) {
    Serial.print("i2s_driver_install error: ");
    Serial.println(err);
    while (true) delay(1000);
  }

  err = i2s_set_pin(I2S_MIC_PORT, &pin_config);
  if (err != ESP_OK) {
    Serial.print("i2s_set_pin error: ");
    Serial.println(err);
    while (true) delay(1000);
  }

  i2s_zero_dma_buffer(I2S_MIC_PORT);
}

// =========================================================
// 5. 마이크 샘플 읽기
// =========================================================

int32_t readMicSample() {
  int32_t rawSample = 0;
  size_t bytesRead = 0;

  i2s_read(
    I2S_MIC_PORT,
    &rawSample,
    sizeof(rawSample),
    &bytesRead,
    portMAX_DELAY
  );

  if (bytesRead != sizeof(rawSample)) {
    return 0;
  }

  // INMP441 24bit 데이터 정렬 보정
  int32_t sample = rawSample >> MIC_SHIFT;

  return sample;
}

// =========================================================
// 6. setup
// =========================================================

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  setupI2SMic();

  Serial.println("# ESP32 INMP441 SERIAL STREAM START");
  Serial.print("# MIC_SAMPLE_RATE=");
  Serial.println(MIC_SAMPLE_RATE);
  Serial.print("# SEND_SAMPLE_RATE=");
  Serial.println(SEND_SAMPLE_RATE);
  Serial.print("# SERIAL_BAUD=");
  Serial.println(SERIAL_BAUD);
  Serial.println("# DATA_FORMAT: one signed integer sample per line");
}

// =========================================================
// 7. loop
// =========================================================

void loop() {
  int32_t sample = readMicSample();

  decimationSum += sample;
  decimationCount++;

  if (decimationCount >= DECIMATION) {
    int32_t avgSample = decimationSum / DECIMATION;

    // Raspberry Pi로 한 줄에 샘플 하나씩 전송
    Serial.println(avgSample);

    sentCount++;
    decimationSum = 0;
    decimationCount = 0;
  }

  // 상태 출력은 데이터 파싱에 방해되지 않게 #으로 시작
  unsigned long now = millis();
  if (now - lastInfoPrint >= 5000) {
    lastInfoPrint = now;
    Serial.print("# sent samples: ");
    Serial.println(sentCount);
  }
}
