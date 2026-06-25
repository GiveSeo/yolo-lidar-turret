# ESP32 표적 명중 판정 모듈

Piezo 센서로 투사체 충격을 감지해 Wi-Fi 로 PC 서버의 `POST /api/hit` 를 호출한다.
서버는 발사 후 10초 안에 이 신호가 오면 **HIT**, 아니면 **MISS** 로 판정한다.

## 동작

1. 부팅 → Wi-Fi(STA) 연결 (연결 중 온보드 LED 점멸)
2. `loop()` 에서 Piezo ADC 값을 폴링
3. 값이 `HIT_THRESHOLD` 초과 + 디바운스 경과 → 충격으로 판정 → `POST /api/hit`
4. body: `{"source":"esp32","ms":<millis>}` (서버는 body 내용 무관, 도착 자체가 신호)

## 설정 (esp32_hit.ino 상단)

```cpp
WIFI_SSID / WIFI_PASS      // 표적이 붙을 Wi-Fi (PC 서버와 같은 네트워크)
SERVER_IP   = "192.168.0.10"   // PC 서버 LAN IP
SERVER_PORT = 8000             // FastAPI 포트
PIEZO_PIN   = 34               // ADC1 입력전용 핀(34/35/36/39 권장)
HIT_THRESHOLD = 600            // 0~4095, 캘리브레이션 필요
DEBOUNCE_MS = 1500             // 한 발에 한 번만 전송
```

> PC 서버 IP 는 `ipconfig`(Windows) 로 확인. 서버는 `--host 0.0.0.0` 으로 실행해
> LAN 에서 접근 가능해야 한다. 방화벽에서 8000 포트 허용 필요할 수 있음.

## Piezo 배선 ⚠️ (보호 필수)

Piezo 는 충격 시 수십 V 의 스파이크를 낼 수 있어 ESP32 ADC 핀(최대 3.3V)을 보호해야 한다.

```
 Piezo(+) ──┬──────────────┬────────► GPIO34 (ADC)
            │              │
          [1MΩ]        [클램프]         ← 1MΩ: 방전/기준, 클램프: 과전압 보호
            │              │
 Piezo(-) ──┴──────────────┴────────► GND
```

- **1MΩ 저항**: Piezo 양단 병렬 — 충전 방전 + DC 기준 확보.
- **클램프(둘 중 하나)**:
  - 3.3V 제너 다이오드 (캐소드 → 신호, 애노드 → GND), 또는
  - 다이오드 2개(1N4148): 신호→3V3 으로 1개, GND→신호 로 1개 (전압 클램프)
- 더 안전하게 하려면 신호 직렬 1~10kΩ + 위 클램프 조합.

> 보호 없이 Piezo 를 ADC 에 직결하면 핀이 손상될 수 있다. 반드시 클램프를 넣을 것.

## 캘리브레이션

1. `esp32_hit.ino` 의 피크 모니터 주석 줄을 해제하고 업로드.
2. 시리얼 모니터(115200)를 열고 표적을 실제로 때려 본다.
3. 충격 시 `peak` 값과 평상시 노이즈 값을 비교해, 그 사이로 `HIT_THRESHOLD` 설정.
4. 주석을 다시 닫고 재업로드.

## Arduino IDE 설정

- 보드: **ESP32 Dev Module** (ESP32 보드 패키지 설치 필요)
- 라이브러리: `WiFi`, `HTTPClient` — ESP32 core 내장(별도 설치 불필요)
- 업로드 후 시리얼 모니터에서 IP/연결/충격 로그 확인.

## 단독 테스트 (센서 없이)

서버만 띄운 상태에서 PC 에서 직접 호출해 HIT 판정 흐름을 확인할 수 있다:

```bash
curl -X POST http://127.0.0.1:8000/api/hit -H "Content-Type: application/json" -d "{}"
```

ESP32 가 보내는 것과 동일한 신호다.
