# AI 자동 조준·발사 시스템 (투사체 발사 시스템)

YOLO 객체 인식 + LiDAR 거리측정으로 표적을 자동 조준·발사하고 명중을 판정하는 시스템.
엣지(라즈베리파이=센싱·중계) ↔ 서버(PC GPU=YOLO 추론·조준)로 역할을 분리했다.

## 아키텍처

```
[표적] →(반사광)→ 카메라
 현장: Raspberry Pi 5 (카메라·LiDAR·STM32 중계)
   └ STM32 F401RE → Pan/Tilt/Trigger 서보 → 대포
        │ TCP 9000 (FRAME/STATUS/LIDAR ↔ CONTROL/RANGE_REQ)
 PC 서버(RTX GPU): FastAPI → YOLOv8n+ByteTrack → 조준엔진 → 발사각 MLP
   ↕ HTTP/WebSocket(8000)   웹 UI(표적 클릭·발사)
   ↑ WiFi /api/hit          ESP32+Piezo(명중 판정)
```

- **PC ↔ Pi**: TCP 소켓(`app/comms/protocol.py`, `pi/protocol.py` 동일 사본 — 둘 다 수정 필요).
  메시지 프레이밍 `[1B type][4B length][payload]`. type: FRAME/STATUS/LIDAR/CONTROL/RANGE_REQ.
- **PC ↔ 웹/ESP32**: FastAPI HTTP + WebSocket(`/video_feed` MJPEG, `/ws`, `/api/*`).
- **Pi ↔ STM32**: UART(USART1), `pi/stm32_link.py` ↔ `firmware/stm32_main.c`.
- **발사 시퀀스(엔진 상태머신, `app/engine.py`)**: AIMING(LiDAR OFF) → RANGING(측거 요청) → FIRING → JUDGING → RESULT.

## 실행 방법

### PC 서버 (실제 YOLO, GPU)
```powershell
cd D:\server
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2
```
- `DETECTOR=demo` 환경변수 주면 색상 블롭 검출기(GPU/가중치 없이 흐름 테스트).
- `--timeout-graceful-shutdown 2` 권장: 안 주면 video_feed/ws 때문에 Ctrl+C로 잘 안 꺼짐.

### Raspberry Pi 5 노드
```bash
cd ~/pi && source venv/bin/activate
python3 rpi_node.py --pc-host <PC_IP> --stm32-port /dev/serial0 --lidar-port /dev/ttyUSB0
# 옵션: --no-stm32 / --no-lidar (단독 테스트), --width/--height/--jpeg-quality
```
- PC IP가 바뀌면 `--pc-host` 갱신. PC·Pi 같은 네트워크 + PC 방화벽 8000/9000 허용.

### 센서/하드웨어 단독 테스트 (Pi에서)
- `pi/stm32_test.py`  — STM32 통신·서보 (`--trigger`로 발사 허용)
- `pi/servo_set.py`   — 원하는 서보를 원하는 각도로 (조립/캘리브레이션)
- `pi/lidar_test.py`  — LiDAR 거리 측정
- `pi/lidar_glare_check.py` — LiDAR ON/OFF 사진 비교(IR 글레어 확인)

### 로컬 통합 테스트 (PC, 실물 없이)
- `python -m tools.mock_rpi --mode synthetic --port 9000 --http http://127.0.0.1:8000 --auto-hit`
- 단위/통합: `python -m tests.test_{protocol,aiming,feedback,pi_protocol_compat,stm32_link,lidar}`

## 하드웨어 핀맵 (Nucleo-F401RE)
- **USART1**: PA9(TX,D8) → Pi 핀10(GPIO15 RX), PA10(RX,D2) ← Pi 핀8(GPIO14 TX), GND 공통
- **TIM3 PWM**: PA6=Pan, PA7=Tilt, PB0=Trigger (50Hz, 펄스 0.5~2.5ms→0~180°)
- 서보 전원 외부 5~6V, 모든 GND 공통. Pi UART 포트 = `/dev/serial0`, LiDAR = `/dev/ttyUSB0`(CP2102).

## 주요 설계 결정 (왜 이렇게)
- **YOLO는 PC GPU에서**: Pi5는 GPU 없어 YOLOv8n도 ~1fps(imgsz 1280). 엣지=센싱, 서버=추론.
- **USART1 사용**: Nucleo PA2/PA3(USART2)가 ST-Link VCP와 충돌 → VCP 무관한 USART1(PA9/PA10).
- **LiDAR 트리거 모드**: noir 카메라가 LiDAR 적외선 반사광을 받아 표적 미검출 → 조준 중 OFF, 측거 시(RANGE_REQ)에만 ON.
- **수신/검출 분리**: TCP 수신 스레드가 YOLO에 막혀 영상 끊김 → 검출은 워커 스레드(`_detector_loop`), 연결별 스레드(최신 우선).
- **선택 위치 락**: ByteTrack id가 카메라 이동으로 흔들림(#35↔#37) → 마지막 선택 위치 최근접 검출로 매 프레임 재바인딩.
- **발사각 MLP**: PyBullet 탄도 시뮬 데이터로 역방향(거리→각도) 학습. tilt 0~45°만 사용(sin2θ 대칭으로 45~90 중복). `models/angle_mlp.pt`.
- **발사 보정**: 발사구가 카메라보다 위 → 거리 시차 보정(atan(0.10/거리)); 좌우 치우침 → `pan_bias_deg`(-6) + `pan_offset_per_m`. 착탄 피드백(`app/feedback.py`)으로 누적 보정.

## 핵심 config (`app/config.py`)
- YoloConfig: `imgsz=1280, conf_threshold=0.20, device="0"` (먼 거리 검출 위해 imgsz↑/conf↓)
- AimingConfig: `center_tolerance_px=45, pan_gain=0.022, tilt 0~45, invert_pan=True, invert_tilt=True`
- BallisticConfig: `tilt_horizontal_deg=0, launcher_above_camera_m=0.10, pan_bias_deg=-6, pan_offset_per_m=0`

## 환경/주의 (트러블슈팅)
- **Pi Bus error**: pip로 opencv/numpy 설치 시 시스템 picamera2와 ABI 충돌. → **apt(python3-opencv/numpy/picamera2/serial)** 사용, venv는 `--system-site-packages`.
- **picamera2 색 반전**: 'RGB888'이 실제 BGR → `Camera.capture()`에서 색변환 안 함.
- **카메라 I/O error(dw9807)**: CSI 리본 접촉불량 → 재장착·재부팅·`rpicam-hello`.
- **RTX 5060 Ti(Blackwell)**: torch는 **cu128** 빌드 필요(`pip install torch --index-url https://download.pytorch.org/whl/cu128`). cu124(sm_90)는 미지원.
- **C: 디스크 부족**: 대용량 설치 시 임시폴더 D:로(`$env:TMP/$env:TEMP="D:\tmp"`).
- STM32 재플래시 확인: `stm32_test` 상태가 `(90,0)`이면 최신(시작 tilt 0), `(90,90)`이면 옛 펌웨어.

## 디렉터리
- `app/` — PC 서버(FastAPI). `main.py`, `engine.py`(상태머신), `comms/`(rpi_link·protocol·esp32_hit), `vision/detector.py`(YOLO+ByteTrack), `aiming/`(controller·angle_model), `config.py`, `state.py`, `feedback.py`.
- `pi/` — 라즈베리파이 노드. `rpi_node.py`, `stm32_link.py`, `lidar.py`, `protocol.py`, 테스트 스크립트들.
- `firmware/` — `stm32_main.c`(F401RE), `esp32_hit/`(명중 판정), `README.md`.
- `train/` — `train_yolo.py`, `train_angle_mlp.py`. `tools/` — mock·재학습·검사. `models/` — best.pt·angle_mlp.pt.
- `web/` — 웹 UI(index.html·app.js·style.css). `tests/` — 단위/통합. `data/` — targets(YOLO)·angle(탄도)·feedback(런타임).
