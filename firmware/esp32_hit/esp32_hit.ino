/**
 * AI 자동 조준 시스템 - ESP32 표적 명중 판정 모듈
 *
 *  Piezo 센서로 투사체 충격을 감지하면 Wi-Fi 로 PC 서버의 POST /api/hit 를 호출한다.
 *  서버는 발사 후 10초간 이 신호를 기다려 HIT/MISS 를 판정한다.
 *
 *  하드웨어:
 *    - ESP32 (DevKit 등)
 *    - Piezo 디스크 센서 (충격 -> 전압 펄스)
 *    - 배터리/USB 전원
 *
 *  Piezo 연결(보호 필수): firmware/esp32_hit/README.md 참고.
 *    Piezo(+) -> ADC 핀(GPIO34), Piezo(-) -> GND
 *    Piezo 양단 병렬 1MΩ(방전) + ADC 핀 클램프(3.3V 제너 또는 다이오드)로 과전압 보호.
 *
 *  Arduino IDE: 보드 "ESP32 Dev Module", 라이브러리 WiFi/HTTPClient(ESP32 core 내장).
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ====== 사용자 설정 ======
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// PC 서버 주소 (FastAPI). PC 의 LAN IP 와 포트.
const char* SERVER_IP   = "192.168.0.10";
const int   SERVER_PORT = 8000;

// Piezo 입력 (ADC1 채널 권장: GPIO32~39 중 입력전용 34/35/36/39)
const int   PIEZO_PIN   = 34;
// 충격 판정 임계값 (0~4095). 환경에 맞게 캘리브레이션(아래 시리얼 모니터로 피크 확인).
int         HIT_THRESHOLD = 600;
// 중복 전송 방지 시간(ms). 한 번의 충격/한 발에 한 번만 전송.
const unsigned long DEBOUNCE_MS = 1500;
// 충격 피크 포착 윈도우(ms). 임계 넘은 뒤 이 시간 동안 최댓값을 찾아 세기로 전송.
const unsigned long PEAK_WINDOW_MS = 40;
// =========================

const int LED_PIN = 2;  // 온보드 LED (상태 표시)
unsigned long lastHitMs = 0;
String hitUrl;

void connectWiFi() {
  Serial.printf("WiFi 연결 중: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
    // 연결 대기 중 LED 점멸
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
  }
  digitalWrite(LED_PIN, HIGH);
  Serial.printf("\n연결됨. IP=%s, 서버=%s\n", WiFi.localIP().toString().c_str(), hitUrl.c_str());
}

// 명중 신호 전송: POST /api/hit  (peak = 충격 세기, 서버가 점수로 환산)
void sendHit(int peak) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  HTTPClient http;
  http.begin(hitUrl);
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"source\":\"esp32\",\"ms\":") + millis()
              + ",\"value\":" + peak + "}";
  int code = http.POST(body);
  Serial.printf("[HIT] POST %s peak=%d -> %d\n", hitUrl.c_str(), peak, code);
  http.end();

  // 명중 표시: LED 짧게 깜빡
  for (int i = 0; i < 6; i++) {
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    delay(60);
  }
  digitalWrite(LED_PIN, HIGH);
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(PIEZO_PIN, INPUT);
  analogReadResolution(12);              // 0~4095
  analogSetPinAttenuation(PIEZO_PIN, ADC_11db);  // 0~3.3V 입력 범위

  hitUrl = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/api/hit";
  connectWiFi();
  Serial.println("준비 완료. 충격 대기 중...");
}

void loop() {
  int v = analogRead(PIEZO_PIN);

  // 임계값 초과 + 디바운스 경과 시 충격으로 판정
  if (v >= HIT_THRESHOLD && (millis() - lastHitMs) > DEBOUNCE_MS) {
    lastHitMs = millis();
    // 충격 윈도우 동안 최댓값(피크)을 포착해 '세기'로 사용
    int peak = v;
    unsigned long t0 = millis();
    while (millis() - t0 < PEAK_WINDOW_MS) {
      int s = analogRead(PIEZO_PIN);
      if (s > peak) peak = s;
    }
    Serial.printf("충격 감지! peak=%d\n", peak);
    sendHit(peak);  // 서버가 peak -> 점수/등급으로 환산
  }

  delay(2);  // ADC 폴링 주기
}
