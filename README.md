# AI 자동 조준 및 명중 판정 — PC 서버

카메라 + YOLOv8 + LiDAR + MLP 로 표적을 자동 조준·발사하고 명중 여부를 판정하는
시스템의 **PC 서버 파트**. 객체 인식, 웹 UI(표적 선택), 발사 각도 추론, 명중 판정을
담당하며 Raspberry Pi(TCP) 및 ESP32(Wi-Fi)와 통신한다.

## 구성요소

```
app/
  main.py              FastAPI 진입점 (REST + WebSocket + MJPEG 스트림)
  config.py            전역 설정 (경로/포트/조준·발사 파라미터/YOLO 옵션)
  state.py             전역 런타임 상태 (락 보호, WebSocket 스냅샷)
  engine.py            조준→측거→발사→판정 상태머신 (별도 스레드)
  vision/detector.py   YOLOv8 래퍼 + DemoDetector(오프라인 검증용 색상블롭)
  aiming/controller.py 조준 오차 P 제어 (객체중심 ↔ 화면중앙)
  aiming/angle_model.py 발사각 MLP 회귀 (정규화 포함 추론)
  comms/protocol.py    Control/Status 패킷 + 메시지 프레이밍
  comms/rpi_link.py    RPi TCP 링크 (프레임 수신→YOLO→상태갱신, 제어 송신)
  comms/esp32_hit.py   ESP32 명중 신호 모니터 (10초 판정)
train/
  train_yolo.py        커스텀 표적 YOLOv8 학습
  train_angle_mlp.py   PyBullet CSV → MLP 학습
tools/
  mock_rpi.py          목 RPi (합성/웹캠 프레임, 서보 시뮬, 자동 명중)
  prepare_ballistic_csv.py  탄도 CSV 컬럼 표준화
  check_angle_model.py / check_yolo.py  학습 모델 sanity check
  integration_test.py  엔드투엔드 자동 테스트
web/                   모바일 반응형 UI (index.html / app.js / style.css)
tests/                 단위 테스트 (protocol / aiming / pi 호환 / stm32 / lidar)

firmware/
  stm32_main.c         STM32 Nucleo-F401RE 펌웨어 (+ README: 서보/UART/배선)
  esp32_hit/           ESP32 명중 판정 (Piezo->WiFi->/api/hit, .ino + README)
pi/                    Raspberry Pi 5 노드 (rpi_node/protocol/stm32_link/lidar + README)
```

## 전체 기기 연동

- **PC ↔ Pi (TCP, 9000)**: 위 표의 프레이밍 프로토콜. PC 가 서버, Pi 가 클라이언트.
- **Pi ↔ STM32 (UART, 115200)**: 경량 바이트 프로토콜 — 자세히는 `firmware/README.md`.
  - 명령 5B `[0xAA][pan][tilt][trigger][chk]`, 상태 4B `[0x55][pan][tilt][chk]`
- **TF-Luna ↔ Pi (UART)**: 9바이트/cm 기본 포맷, Pi 가 mm 로 변환해 PC 로 전송.
- 배선·설정: STM32 는 `firmware/README.md`, Pi 는 `pi/README.md` 참고.

## 설치 (완료된 환경)

- Python 3.12, RTX 4060 (CUDA 12.4), PyTorch 2.6+cu124, Ultralytics 8.4

```powershell
# 가상환경 + PyTorch(CUDA) + 의존성  (C: 용량 부족 대비 임시폴더를 D: 로)
python -m venv .venv
$env:TMP="D:\tmp"; $env:TEMP="D:\tmp"; $env:PIP_CACHE_DIR="D:\pip-cache"
.\.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\python -m pip install -r requirements.txt
```

> ⚠️ C: 드라이브 여유 공간이 0.5GB 미만이라, pip 임시/캐시를 D: 로 지정해야
> torch(2.5GB) 설치가 가능합니다. 위 `$env:TMP/TEMP/PIP_CACHE_DIR` 설정 필수.

## 실행

```powershell
# 1) 서버 (실제 YOLO)
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 데모 검출기(가중치/웹캠 없이 합성표적)로 띄우려면:
$env:DETECTOR="demo"; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# 2) 브라우저 접속 (PC/모바일 동일 Wi-Fi)
#    PC:    http://127.0.0.1:8000
#    모바일: http://<PC_IP>:8000
```

영상 위 표적을 **탭/클릭**하여 선택 → **발사 시퀀스 시작** 버튼 → 자동 조준·측거·발사 →
10초 내 ESP32 명중 신호 수신 시 **HIT**, 아니면 **MISS**.

## 통신 프로토콜

| 패킷 | 포맷 | 방향 |
|------|------|------|
| ControlPacket {pan_target, tilt_target, trigger} | `<iii` (12B) | PC→RPi→STM32 |
| StatusPacket {pan_current, tilt_current} | `<ii` (8B) | STM32→RPi→PC |
| LiDAR 거리(mm) | `<i` (4B) | RPi→PC |

- **RPi↔PC (TCP, 기본 9000)**: PC 가 listen, RPi 가 접속. 메시지 프레이밍
  `[1B type][4B length][payload]`, type ∈ {FRAME, STATUS, LIDAR, CONTROL}.
- **ESP32→PC (Wi-Fi)**: `POST /api/hit` 로 명중 신호. body 무관.

### REST/WS 엔드포인트
- `GET /` UI, `GET /video_feed` MJPEG, `WS /ws` 상태 스냅샷(10Hz)
- `GET /api/state`, `POST /api/select/{id}`, `POST /api/select_at`,
  `POST /api/clear_selection`, `POST /api/engage`, `POST /api/hit`

## 학습

```powershell
# 커스텀 표적 YOLOv8 검출 (Roboflow 데이터셋, 폴리곤 라벨 -> 박스 자동변환)
.\.venv\Scripts\python -m train.train_yolo --data data/targets/data.yaml --model yolov8n.pt --epochs 100
#  → 학습 후 best.pt 가 models/yolo/best.pt 로 복사되어 서버가 자동 로드

# 발사각 MLP (탄도 CSV: 역방향 거리->각도)
.\.venv\Scripts\python -m tools.prepare_ballistic_csv "원본.csv"   # 컬럼 표준화 -> data/angle/projectile.csv
.\.venv\Scripts\python -m train.train_angle_mlp --csv data/angle/projectile.csv `
    --inputs weight,air_resistance,landing_distance --outputs angle --epochs 800 --batch 16
#  → models/angle_mlp.pt 저장
.\.venv\Scripts\python -m tools.check_angle_model   # 설정 발사체로 거리별 발사각 sanity check
```

### 탄도 발사각 모델 (중요)

탄도 CSV 는 **순방향** `(weight, air_resistance, angle) -> landing_distance` 시뮬이다.
시스템은 **역방향** `(weight, air_resistance, distance) -> angle` 을 학습한다.

- 동작: 조준으로 표적을 화면 중앙에 맞춰(LiDAR 정조준) 거리를 잰 뒤, **pan(좌우)은
  조준값 유지**, **tilt(상하)만** 발사각으로 바꿔 발사. `tilt_servo = tilt_horizontal_deg
  + up_sign * angle` (`app/config.py: BallisticConfig`).
- 발사체 스펙(`projectile_weight`, `projectile_air_resistance`)은 config 고정값(기본
  0.003kg / 0.05). 환경변수 `PROJ_WEIGHT`, `PROJ_AIR` 로 덮어쓰기 가능.
- ⚠️ 역문제 주의: 공기저항=0 구간은 ~35°에서 착탄거리가 정점이라 같은 거리에 두 각도가
  대응(비단조) → 회귀 정확도 저하. 실제 발사체처럼 공기저항>0(단조 감소) 구간에서는 양호.
  데이터를 늘리거나 발사체 스펙을 고정해 학습하면 정확도가 오른다.

### 피드백 기반 보정 (sim-to-real)

시뮬 학습 모델의 실제 오차를 운영자 피드백으로 보정한다 (`app/feedback.py`).

- 발사 후 웹 UI 에서 **명중 / 앞(짧음) / 뒤(김) + 오차(cm)** 입력 → `POST /api/feedback`
- **잔차 보정**: `residual = used_angle − model(weight, air, 실제착탄거리)`,
  `tilt_bias += bias_lr × residual` (모델 기준이라 탄도 단조성과 무관하게 부호 정확)
- `tilt_bias` 는 `data/feedback/calibration.json` 에 영속, 발사각에 가산됨
- 모든 발사 실측은 `data/feedback/shots.csv` 에 누적(재학습용)
- **재학습**: 로그가 쌓이면 시뮬+실측 혼합으로 모델 갱신 후 bias 리셋
  ```powershell
  .\.venv\Scripts\python -m tools.retrain_with_feedback --reset-bias
  ```

## 검증 (테스트)

```powershell
# 단위 테스트
.\.venv\Scripts\python -m tests.test_protocol
.\.venv\Scripts\python -m tests.test_aiming

# 엔드투엔드 (3개 터미널 또는 백그라운드)
$env:DETECTOR="demo"; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000   # 서버
.\.venv\Scripts\python -m tools.mock_rpi --mode synthetic --auto-hit              # 목 RPi
.\.venv\Scripts\python -m tools.integration_test                                  # 결과 HIT 확인(exit 0)
```

검증 완료 항목: 패킷 라운드트립 · 조준 P제어 · MLP 학습(GPU)/추론 · YOLO GPU 추론 ·
TCP 프레임/제어/상태 왕복 · 표적 선택 · 발사 시퀀스 상태머신 · ESP32 명중 판정(HIT).

## 실기기 연동 시 조정 포인트

- `app/config.py` 의 `AimingConfig`: gain 부호/`invert_tilt`/`max_step_deg` 를 실제
  짐벌 방향과 서보 응답에 맞춰 보정.
- `mock_rpi.py` 의 `PX_PER_DEG`/`WORLD_*` 는 시뮬레이션 값 → 실제 RPi 펌웨어가
  동일 프로토콜로 FRAME/STATUS/LIDAR 송신, CONTROL 수신하도록 구현.
- MLP 입력 피처는 학습 CSV 컬럼명을 그대로 사용 (`engine._predict_angles` 의
  `feat_map` 이 흔한 이름들을 매핑; 새 컬럼명이면 매핑 추가).
