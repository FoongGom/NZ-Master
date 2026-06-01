
/*
ESP32 WebSocket ANC Prototype
구조: INMP441 -> ESP32 -> WebSocket -> Raspberry Pi -> WebSocket -> ESP32 -> GPIO25 DAC -> PAM8403 -> Speaker

필요 라이브러리:
Arduino IDE Library Manager에서 "WebSockets by Markus Sattler" 설치

주의:
완전 상쇄용이 아니라 일부 저감 가능성 확인용입니다.
WebSocket 왕복 지연 때문에 순간 충격음보다 지속 저주파 소리에 더 적합합니다.
*/

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include "driver/i2s.h"
#include "driver/dac.h"

// ===================== 사용자 설정 =====================
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* RPI_HOST = "192.168.0.25";   // Raspberry Pi IP로 수정
const uint16_t RPI_PORT = 8765;
const char* WS_PATH = "/";

// ===================== INMP441 핀 =====================
#define I2S_MIC_PORT I2S_NUM_0
#define I2S_MIC_SCK  14
#define I2S_MIC_WS   15
#define I2S_MIC_SD   32

// GPIO25 = 일반 ESP32 DAC1
#define DAC_OUT_PIN 25

// ===================== 샘플 설정 =====================
const int MIC_SAMPLE_RATE = 8000;
const int SEND_SAMPLE_RATE = 1000;
const int DECIMATION = MIC_SAMPLE_RATE / SEND_SAMPLE_RATE;
const int FRAME_SIZE = 64;

const int MIC_SHIFT = 14;
const float MIC_SCALE = 3.0;
const int DAC_LIMIT = 80;

WebSocketsClient webSocket;

int16_t micFrame[FRAME_SIZE];
int16_t antiFrame[FRAME_SIZE];

volatile bool wsConnected = false;
volatile bool hasAntiFrame = false;

int frameIndex = 0;
int decimationCount = 0;
int32_t decimationSum = 0;

unsigned long sentFrames = 0;
unsigned long receivedFrames = 0;
unsigned long lastStatus = 0;

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

int32_t readMicSample() {
  int32_t rawSample = 0;
  size_t bytesRead = 0;

  i2s_read(I2S_MIC_PORT, &rawSample, sizeof(rawSample), &bytesRead, portMAX_DELAY);

  if (bytesRead != sizeof(rawSample)) return 0;

  return rawSample >> MIC_SHIFT;
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  if (type == WStype_CONNECTED) {
    wsConnected = true;
    Serial.println("[WS] Connected");
  } else if (type == WStype_DISCONNECTED) {
    wsConnected = false;
    Serial.println("[WS] Disconnected");
  } else if (type == WStype_BIN) {
    if (length == FRAME_SIZE * sizeof(int16_t)) {
      memcpy((void*)antiFrame, payload, length);
      hasAntiFrame = true;
      receivedFrames++;
    }
  } else if (type == WStype_TEXT) {
    Serial.print("[WS TEXT] ");
    Serial.println((char*)payload);
  }
}

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

void playAntiFrame() {
  if (!hasAntiFrame) return;

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

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("ESP32 WebSocket INMP441 ANC Prototype");

  setupI2SMic();

  dac_output_enable(DAC_CHANNEL_1); // GPIO25
  dacWrite(DAC_OUT_PIN, 128);

  connectWiFi();

  webSocket.begin(RPI_HOST, RPI_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(2000);
}

void loop() {
  webSocket.loop();

  int32_t micSample = readMicSample();

  decimationSum += micSample;
  decimationCount++;

  if (decimationCount >= DECIMATION) {
    int32_t avgSample = decimationSum / DECIMATION;

    float scaled = avgSample * MIC_SCALE;
    if (scaled > 32767.0) scaled = 32767.0;
    if (scaled < -32768.0) scaled = -32768.0;

    micFrame[frameIndex] = (int16_t)scaled;
    frameIndex++;

    decimationSum = 0;
    decimationCount = 0;
  }

  if (frameIndex >= FRAME_SIZE) {
    frameIndex = 0;

    if (wsConnected) {
      webSocket.sendBIN((uint8_t*)micFrame, FRAME_SIZE * sizeof(int16_t));
      sentFrames++;
    }
  }

  playAntiFrame();

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
