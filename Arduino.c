// =====================================================
// sensor_input.ino
// ESP32 층간소음 센서 입력 시스템
// 마이크 + BNO085
// 라즈베리파이로 Serial 전송
// =====================================================

#include <Wire.h>
#include <Adafruit_BNO08x.h>

// ---------------- BNO085 ----------------
Adafruit_BNO08x bno08x;
sh2_SensorValue_t sensorValue;

// ---------------- 핀 설정 ----------------
#define MIC_PIN 34

// ---------------- 데이터 ----------------
int micValue = 0;

float vibX = 0;
float vibY = 0;
float vibZ = 0;

// ---------------- 스무딩 ----------------
float smoothX = 0;
float smoothY = 0;
float smoothZ = 0;

// =====================================================
// LOW PASS FILTER
// =====================================================
float smooth(float previous, float current)
{
    float alpha = 0.8;

    return alpha * previous + (1.0 - alpha) * current;
}

// =====================================================
// BNO085 초기화
// =====================================================
void initBNO()
{
    if (!bno08x.begin_I2C())
    {
        Serial.println("BNO085 ERROR");

        while (1)
        {
            delay(10);
        }
    }

    if (!bno08x.enableReport(SH2_ACCELEROMETER))
    {
        Serial.println("ACCEL ERROR");

        while (1)
        {
            delay(10);
        }
    }

    Serial.println("BNO085 READY");
}

// =====================================================
// 마이크 읽기
// =====================================================
void readMic()
{
    micValue = analogRead(MIC_PIN);
}

// =====================================================
// 진동 읽기
// =====================================================
void readVibration()
{
    if (bno08x.getSensorEvent(&sensorValue))
    {
        if (sensorValue.sensorId == SH2_ACCELEROMETER)
        {
            vibX = sensorValue.un.accelerometer.x;
            vibY = sensorValue.un.accelerometer.y;
            vibZ = sensorValue.un.accelerometer.z;
        }
    }
}

// =====================================================
// 안정화
// =====================================================
void stabilize()
{
    smoothX = smooth(smoothX, vibX);
    smoothY = smooth(smoothY, vibY);
    smoothZ = smooth(smoothZ, vibZ);
}

// =====================================================
// 라즈베리파이 전송
// =====================================================
void sendData()
{
    // CSV 형식 전송
    // mic,x,y,z

    Serial.print(micValue);
    Serial.print(",");

    Serial.print(smoothX);
    Serial.print(",");

    Serial.print(smoothY);
    Serial.print(",");

    Serial.println(smoothZ);
}

// =====================================================
// SETUP
// =====================================================
void setup()
{
    Serial.begin(115200);

    delay(2000);

    Serial.println("SYSTEM START");

    Wire.begin();

    initBNO();
}

// =====================================================
// LOOP
// =====================================================
void loop()
{
    // 1. 마이크 읽기
    readMic();

    // 2. 진동 읽기
    readVibration();

    // 3. 안정화
    stabilize();

    // 4. 라즈베리파이 전송
    sendData();

    // 샘플링 속도
    delay(1);
}