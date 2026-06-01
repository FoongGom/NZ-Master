
/*
  파일명: esp32_websocket_inmp441_anc.ino

  =========================================================
  이 코드가 하는 일
  =========================================================

  이 코드는 ESP32에서 실행하는 코드입니다.

  쉽게 말하면 ESP32가 하는 일은 3가지입니다.

  1. INMP441 마이크로 소리를 듣는다.
  2. 들은 소리 데이터를 Wi-Fi로 라즈베리파이에 보낸다.
  3. 라즈베리파이가 계산해서 보내준 "반대 소리"를 스피커로 출력한다.

  전체 흐름은 아래와 같습니다.

  [방 안 소리]
      ↓
  [INMP441 마이크]
      ↓
  [ESP32]
      ↓ Wi-Fi / WebSocket
  [Raspberry Pi]
      ↓ Wi-Fi / WebSocket
  [ESP32]
      ↓ GPIO25 DAC 출력
  [PAM8403 앰프]
      ↓
  [스피커]

  =========================================================
  왜 라즈베리파이로 보내는가?
  =========================================================

  ESP32는 마이크와 스피커를 연결하기 좋지만,
  복잡한 소리 분석이나 DSP 계산은 라즈베리파이가 더 편합니다.

  그래서 역할을 나눴습니다.

  ESP32:
  - 마이크 입력 담당
  - 스피커 출력 담당

  Raspberry Pi:
  - 소리 분석 담당
  - 반대 위상 신호 계산 담당

  =========================================================
  중요한 한계
  =========================================================

  이 구조는 Wi-Fi로 데이터를 왕복하기 때문에 지연이 있습니다.
  그래서 발소리처럼 "쿵!" 하고 짧게 끝나는 소리를 완벽히 잡기는 어렵습니다.

  대신 아래처럼 계속 이어지는 소리에 더 적합합니다.

  - 세탁기 웅웅거림
  - 환풍기 소리
  - 사람 말소리의 낮은 성분
  - 지속적인 저주파 소리

  =========================================================
  필요한 Arduino 라이브러리
  =========================================================

  Arduino IDE에서 아래 라이브러리를 설치해야 합니다.

  - WebSockets by Markus Sattler
*/

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include "driver/i2s.h"
#include "driver/dac.h"


// =========================================================
// 1. Wi-Fi와 라즈베리파이 주소 설정
// =========================================================
//
// ESP32가 라즈베리파이와 통신하려면 같은 Wi-Fi에 연결되어야 합니다.
//
// WIFI_SSID:
// - ESP32가 접속할 Wi-Fi 이름
//
// WIFI_PASSWORD:
// - Wi-Fi 비밀번호
//
// RPI_HOST:
// - 라즈베리파이의 IP 주소
// - 라즈베리파이에서 hostname -I 명령어로 확인할 수 있습니다.
//
// RPI_PORT:
// - 라즈베리파이 Python 서버가 열어두는 포트 번호
// - Python 코드도 기본값 8765를 사용합니다.

const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* RPI_HOST = "192.168.0.25";
const uint16_t RPI_PORT = 8765;
const char* WS_PATH = "/";


// =========================================================
// 2. INMP441 마이크 핀 설정
// =========================================================
//
// INMP441은 일반 아날로그 마이크가 아닙니다.
// I2S라는 디지털 통신 방식으로 소리 데이터를 보냅니다.
//
// 그래서 ESP32의 I2S 입력 기능을 사용합니다.
//
// 연결 방법:
//
// INMP441 VDD  → ESP32 3.3V
// INMP441 GND  → ESP32 GND
// INMP441 SCK  → ESP32 GPIO14
// INMP441 WS   → ESP32 GPIO15
// INMP441 SD   → ESP32 GPIO32
// INMP441 L/R  → GND
//
// SCK:
// - 마이크 데이터가 넘어가는 속도를 맞추는 클럭 선
//
// WS:
// - 왼쪽/오른쪽 채널을 구분하는 선
//
// SD:
// - 실제 마이크 소리 데이터가 들어오는 선

#define I2S_MIC_PORT I2S_NUM_0
#define I2S_MIC_SCK  14
#define I2S_MIC_WS   15
#define I2S_MIC_SD   32


// =========================================================
// 3. 스피커 출력 핀 설정
// =========================================================
//
// PAM8403은 아날로그 오디오 신호를 증폭하는 앰프입니다.
// 그래서 ESP32에서 아날로그 비슷한 신호를 내보내야 합니다.
//
// 일반 ESP32 DevKit에는 DAC 출력 핀이 있습니다.
//
// GPIO25 = DAC1
//
// 연결 방법:
//
// ESP32 GPIO25
// → 1uF~10uF 커플링 캐패시터 권장
// → PAM8403 L-IN 또는 R-IN
//
// ESP32 GND
// → PAM8403 GND
//
// PAM8403 OUT
// → 스피커
//
// 주의:
// ESP32-S3, ESP32-C3 같은 일부 보드는 내장 DAC가 없을 수 있습니다.
// 이 코드는 일반 ESP32 DevKit 기준입니다.

#define DAC_OUT_PIN 25


// =========================================================
// 4. 샘플링 설정
// =========================================================
//
// 샘플링이란?
// - 소리를 숫자로 바꾸기 위해 1초에 몇 번 측정할지 정하는 것입니다.
//
// MIC_SAMPLE_RATE = 8000
// - ESP32가 마이크에서 1초에 8000번 소리를 읽습니다.
//
// SEND_SAMPLE_RATE = 1000
// - 라즈베리파이에 보낼 때는 1초에 1000개 데이터만 보냅니다.
//
// 왜 줄이는가?
// - Wi-Fi로 너무 많은 데이터를 보내면 느려지고 지연이 커집니다.
// - 우리는 고주파보다 저주파 층간소음을 보려는 것이므로 1000Hz로 낮춰도 1차 실험에는 충분합니다.
//
// FRAME_SIZE = 64
// - 샘플 64개를 모아서 한 번에 라즈베리파이로 보냅니다.
// - 1000Hz 기준 64개는 약 0.064초입니다.

const int MIC_SAMPLE_RATE = 8000;
const int SEND_SAMPLE_RATE = 1000;
const int DECIMATION = MIC_SAMPLE_RATE / SEND_SAMPLE_RATE;
const int FRAME_SIZE = 64;


// =========================================================
// 5. 마이크 값 크기 조절 설정
// =========================================================
//
// INMP441에서 들어오는 원본 값은 매우 큽니다.
// 그래서 값을 줄여서 사용합니다.
//
// MIC_SHIFT:
// - 원본 데이터를 오른쪽으로 밀어서 값을 줄입니다.
// - 소리가 너무 작게 들어오면 14를 12로 바꿔볼 수 있습니다.
// - 소리가 너무 크게 들어오면 14를 16으로 바꿔볼 수 있습니다.
//
// MIC_SCALE:
// - 라즈베리파이에 보낼 값의 크기를 조절합니다.
// - 너무 작으면 키우고, 너무 크면 줄입니다.
//
// DAC_LIMIT:
// - 스피커 출력이 너무 커지지 않게 제한합니다.
// - 하울링이나 찢어지는 소리를 막기 위한 안전장치입니다.

const int MIC_SHIFT = 14;
const float MIC_SCALE = 3.0;
const int DAC_LIMIT = 80;


// =========================================================
// 6. 전역 변수
// =========================================================

WebSocketsClient webSocket;

// ESP32가 라즈베리파이로 보낼 마이크 데이터 묶음
int16_t micFrame[FRAME_SIZE];

// 라즈베리파이가 계산해서 다시 보내주는 상쇄 소리 데이터 묶음
int16_t antiFrame[FRAME_SIZE];

// WebSocket 연결 상태
volatile bool wsConnected = false;

// 라즈베리파이에서 받은 상쇄 데이터가 있는지 표시
volatile bool hasAntiFrame = false;

// micFrame에 현재 몇 번째 샘플을 넣고 있는지
int frameIndex = 0;

// 8000Hz 데이터를 1000Hz로 줄이기 위한 누적 변수
int decimationCount = 0;
int32_t decimationSum = 0;

// 상태 확인용 카운터
unsigned long sentFrames = 0;
unsigned long receivedFrames = 0;
unsigned long lastStatus = 0;


// =========================================================
// 7. INMP441 마이크 초기화
// =========================================================
//
// 이 함수는 ESP32가 INMP441 마이크를 읽을 수 있도록 I2S 기능을 켭니다.
// setup()에서 한 번만 실행됩니다.

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
// 8. 마이크 샘플 1개 읽기
// =========================================================
//
// INMP441에서 소리 데이터 1개를 읽어옵니다.
// 읽은 값은 너무 크기 때문에 MIC_SHIFT로 줄여서 반환합니다.

int32_t readMicSample() {
  int32_t rawSample = 0;
  size_t bytesRead = 0;

  i2s_read(I2S_MIC_PORT, &rawSample, sizeof(rawSample), &bytesRead, portMAX_DELAY);

  if (bytesRead != sizeof(rawSample)) {
    return 0;
  }

  return rawSample >> MIC_SHIFT;
}


// =========================================================
// 9. WebSocket 이벤트 처리
// =========================================================
//
// ESP32와 라즈베리파이 사이에서 어떤 일이 생겼을 때 자동으로 실행됩니다.
//
// 연결됨:
// - wsConnected = true
//
// 연결 끊김:
// - wsConnected = false
//
// binary 데이터 받음:
// - 라즈베리파이가 계산한 상쇄 신호를 받은 것
// - antiFrame에 저장한 뒤 스피커 출력 준비

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  if (type == WStype_CONNECTED) {
    wsConnected = true;
    Serial.println("[WS] Connected");
  }

  else if (type == WStype_DISCONNECTED) {
    wsConnected = false;
    Serial.println("[WS] Disconnected");
  }

  else if (type == WStype_BIN) {
    if (length == FRAME_SIZE * sizeof(int16_t)) {
      memcpy((void*)antiFrame, payload, length);
      hasAntiFrame = true;
      receivedFrames++;
    }
  }

  else if (type == WStype_TEXT) {
    Serial.print("[WS TEXT] ");
    Serial.println((char*)payload);
  }
}


// =========================================================
// 10. Wi-Fi 연결
// =========================================================
//
// ESP32를 Wi-Fi에 연결합니다.
// 연결될 때까지 기다립니다.

void connectWiFi() {
  Serial.print("Connecting WiFi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}


// =========================================================
// 11. 라즈베리파이에서 받은 상쇄 신호를 스피커로 출력
// =========================================================
//
// 라즈베리파이에서 받은 antiFrame에는 "반대 위상 소리" 데이터가 들어 있습니다.
//
// 이 함수는 그 값을 ESP32 DAC 범위인 0~255로 바꿔서 GPIO25로 출력합니다.
//
// GPIO25 출력
// → PAM8403 앰프
// → 스피커
//
// DAC에서 128은 가운데값입니다.
// 소리는 128을 중심으로 위아래로 흔들리는 값으로 출력됩니다.

void playAntiFrame() {
  if (!hasAntiFrame) {
    return;
  }

  hasAntiFrame = false;

  int samplePeriodUs = 1000000 / SEND_SAMPLE_RATE;

  for (int i = 0; i < FRAME_SIZE; i++) {
    int16_t sample = antiFrame[i];

    int dacValue = 128 + (sample / 256);

    if (dacValue > 128 + DAC_LIMIT) dacValue = 128 + DAC_LIMIT;
    if (dacValue < 128 - DAC_LIMIT) dacValue = 128 - DAC_LIMIT;
    if (dacValue < 0) dacValue = 0;
    if (dacValue > 255) dacValue = 255;

    dacWrite(DAC_OUT_PIN, dacValue);

    delayMicroseconds(samplePeriodUs);
  }
}


// =========================================================
// 12. setup
// =========================================================
//
// ESP32가 켜졌을 때 한 번만 실행됩니다.
//
// 실행 순서:
// 1. 시리얼 모니터 시작
// 2. 마이크 초기화
// 3. DAC 출력 준비
// 4. Wi-Fi 연결
// 5. Raspberry Pi WebSocket 서버에 연결

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("ESP32 WebSocket INMP441 ANC Prototype");

  setupI2SMic();

  dac_output_enable(DAC_CHANNEL_1);
  dacWrite(DAC_OUT_PIN, 128);

  connectWiFi();

  webSocket.begin(RPI_HOST, RPI_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(2000);
}


// =========================================================
// 13. loop
// =========================================================
//
// ESP32가 켜져 있는 동안 계속 반복됩니다.
//
// 반복 내용:
// 1. WebSocket 연결 유지
// 2. 마이크 샘플 읽기
// 3. 8000Hz 데이터를 1000Hz로 줄이기
// 4. 64개 샘플이 모이면 Raspberry Pi로 전송
// 5. Raspberry Pi에서 상쇄 신호가 오면 스피커로 출력
// 6. 1초마다 상태 출력

void loop() {
  webSocket.loop();

  // 1. 마이크에서 소리 샘플 1개 읽기
  int32_t micSample = readMicSample();

  // 2. 8000Hz 데이터를 1000Hz로 줄이기 위해 샘플을 누적
  decimationSum += micSample;
  decimationCount++;

  // 3. 샘플 8개가 모이면 평균을 내서 1개 샘플로 사용
  if (decimationCount >= DECIMATION) {
    int32_t avgSample = decimationSum / DECIMATION;

    // 4. 라즈베리파이에 보내기 좋은 크기로 조절
    float scaled = avgSample * MIC_SCALE;
    if (scaled > 32767.0) scaled = 32767.0;
    if (scaled < -32768.0) scaled = -32768.0;

    // 5. micFrame에 저장
    micFrame[frameIndex] = (int16_t)scaled;
    frameIndex++;

    decimationSum = 0;
    decimationCount = 0;
  }

  // 6. micFrame에 64개 샘플이 모이면 라즈베리파이로 전송
  if (frameIndex >= FRAME_SIZE) {
    frameIndex = 0;

    if (wsConnected) {
      webSocket.sendBIN((uint8_t*)micFrame, FRAME_SIZE * sizeof(int16_t));
      sentFrames++;
    }
  }

  // 7. 라즈베리파이에서 받은 상쇄 소리가 있으면 출력
  playAntiFrame();

  // 8. 상태 확인용 출력
  unsigned long now = millis();
  if (now - lastStatus >= 1000) {
    lastStatus = now;

    Serial.print("WS=");
    Serial.print(wsConnected ? "connected" : "disconnected");
    Serial.print(" | sent=");
    Serial.print(sentFrames);
    Serial.print(" | received=");
    Serial.print(receivedFrames);
    Serial.print(" | RSSI=");
    Serial.println(WiFi.RSSI());
  }
}
