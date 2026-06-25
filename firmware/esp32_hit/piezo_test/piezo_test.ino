/**
 * Piezo 센서 단독 테스트 (WiFi/서버 불필요)
 *
 *  목적: 1) 배선이 맞는지   2) 충격 시 값이 튀는지   3) HIT_THRESHOLD 캘리브레이션
 *
 *  모듈(VCC/GND/AO/DO) 배선 — ⚠️ VCC는 반드시 3.3V (5V 금지):
 *    VCC -> 3V3,  GND -> GND,  AO -> GPIO34,  DO -> GPIO27(선택)
 *
 *  시리얼 모니터: 115200 baud
 *    - 평상시 노이즈(base)와 충격 시 peak 값을 비교해
 *      그 "사이" 값으로 esp32_hit.ino 의 HIT_THRESHOLD 를 정한다.
 */

const int PIEZO_AO = 34;   // 모듈 AO (아날로그) -> 입력전용 ADC1
const int PIEZO_DO = 27;   // 모듈 DO (디지털, 선택) - 안 쓰면 무시됨
const int LED_PIN  = 2;    // 온보드 LED

int peak = 0;              // 관측된 최대값
unsigned long lastReport = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(LED_PIN, OUTPUT);
  pinMode(PIEZO_AO, INPUT);
  pinMode(PIEZO_DO, INPUT);
  analogReadResolution(12);                    // 0~4095
  analogSetPinAttenuation(PIEZO_AO, ADC_11db); // 0~3.3V
  Serial.println("\n=== Piezo 단독 테스트 시작 ===");
  Serial.println("센서를 톡톡 쳐보세요. 충격 시 peak 가 올라갑니다.");
  Serial.println("base(평상시) 와 peak(충격) 차이를 보고 THRESHOLD 결정.\n");
}

void loop() {
  int v   = analogRead(PIEZO_AO);   // 아날로그 값
  int dig = digitalRead(PIEZO_DO);  // 디지털(비교기) 값: 임계 넘으면 HIGH/LOW

  if (v > peak) peak = v;          // 이번 구간 최대값 추적

  // DO 가 트리거되면 LED 로 즉시 표시(모듈 가변저항으로 임계 조절)
  digitalWrite(LED_PIN, dig);

  // 200ms 마다 현재값/피크 출력
  if (millis() - lastReport > 200) {
    lastReport = millis();
    Serial.printf("AO now=%4d  peak=%4d   DO=%d\n", v, peak, dig);
    peak = 0;                      // 구간 피크 리셋
  }

  delay(2);
}
