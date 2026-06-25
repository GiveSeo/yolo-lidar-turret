# AI 자동 조준·발사 시스템 — 프로젝트 명세 (CLAUDE.md)

> 이 문서는 코딩 어시스턴트(Claude Code)가 프로젝트를 빠르게 파악하도록 작성한 상세 명세다.
> 사람용 개요는 `README.md`, 발표/문서용 정리는 `DOCS.md` 참고.

YOLO 객체 인식 + LiDAR 거리측정으로 표적을 자동 조준·발사하고 명중을 판정하는 시스템.
**엣지(라즈베리파이=센싱·중계) ↔ 서버(PC GPU=YOLO 추론·조준)** 로 역할을 분리했다.

---

## 1. 아키텍처

```
[표적] →반사광→ 카메라
 현장: Raspberry Pi 5 (pi/)        ── TCP 9000 ──   PC 서버 (app/, RTX GPU)
   ├ PiCamera ──FRAME(JPEG)─────────────────────▶  YOLOv8n+ByteTrack 검출
   ├ TF-Luna LiDAR ──LIDAR(mm)──────────────────▶  조준 엔진(상태머신)
   └ STM32 F401RE ◀──CONTROL/RANGE_REQ──────────   발사각 계산(실측표)
        │ UART(USART1)                              ↕ HTTP/WS 8000  웹 UI(web/)
        └ Pan/Tilt/Trigger 서보 → 대포              ↑ WiFi /api/hit  ESP32+피에조(명중판정)
                                                    ↕ HTTPS          GMS→Claude(AI 분석/챗봇)
```

- **PC ↔ Pi**: TCP 소켓. 프레이밍 `[1B type][4B length(BE)][payload]`. (`app/comms/protocol.py` ≡ `pi/protocol.py`, **동일 사본 — 둘 다 수정 필요**)
- **PC ↔ 웹/ESP32**: FastAPI HTTP + WebSocket (`/video_feed` MJPEG, `/ws`, `/api/*`).
- **Pi ↔ STM32**: UART(USART1), `pi/stm32_link.py` ↔ `firmware/stm32_main.c`.

---

## 2. 디렉터리 / 파일 맵

### `app/` — PC 서버 (FastAPI)
| 파일 | 책임 |
|------|------|
| `main.py` | FastAPI 진입점. lifespan에서 detector/link/engine/hit_monitor/feedback/db 초기화. 모든 REST·WS·MJPEG 엔드포인트. |
| `engine.py` | **조준/발사 오케스트레이션 상태머신** (별도 스레드). `_aim`→`_range`→`_fire`→`_judge`→`_recenter`. 발사각 산출 `_predict_angles`. |
| `state.py` | 전역 런타임 상태(`SystemState`, RLock). `Detection` 데이터클래스. `snapshot()`이 WS로 나감. |
| `config.py` | **모든 설정 단일 출처**. dataclass(Network/Aiming/Fire/Ballistic/Yolo/Score/Ai). 환경변수 오버라이드. |
| `comms/protocol.py` | TCP 프레이밍 + 패킷(ControlPacket `<iii`, StatusPacket `<ii`, LIDAR `<i`). `MsgType` enum. |
| `comms/rpi_link.py` | Pi와 TCP 링크. 수신 스레드(FRAME 디코드) + **검출 워커 스레드 분리**. 위치기반 선택 락/자동 재선택. `send_control()`. |
| `comms/esp32_hit.py` | `HitMonitor` — ESP32 명중 신호 대기(Event). `arm`/`signal_hit`/`wait_for_hit`/`poll_hit`. |
| `vision/detector.py` | `Detector`(YOLO+ByteTrack) / `DemoDetector`(색상 블롭, GPU 없이 테스트). `get_detector()` env `DETECTOR`. |
| `aiming/controller.py` | `compute_error`(중앙 오차, 박스폭 적응 허용오차) / `next_aim_angles`(P제어). |
| `aiming/ballistic_solver.py` | **발사각 계산 핵심**. `solve_angle_table`(실측표 보간) / `solve_angle`(해석식 물리) / `forward_distance`. |
| `aiming/angle_model.py` | `AnglePredictor`(MLP 회귀 로드/추론). 현재 미사용(표 우선). |
| `feedback.py` | 착탄 피드백 → `tilt_bias`/`pan_bias` 누적(EMA). `calibration.json` 영속. `reset_bias`. |
| `db.py` | 발사 로그 SQLite(`shots.db`). `insert_shot`/`fetch_shots`/`compute_stats`/`clear_shots`. |
| `scoring.py` | 피에조 세기 → 점수(0~100)/등급(S~F). |
| `ai_report.py` | 발사 로그 집계 → GMS(Claude) 한 번에 요약. |
| `ai_chat.py` | 발사 로그 챗봇. (페르소나+stats+shots) system, 대화 messages → Claude. |

### `pi/` — 라즈베리파이 노드
| 파일 | 책임 |
|------|------|
| `rpi_node.py` | 메인 노드. 카메라/LiDAR/STM32/HAT 스레드 + PC 재연결. `Camera` 클래스(picamera2/opencv). |
| `protocol.py` | `app/comms/protocol.py`의 **동일 사본** (Pi에서 import). |
| `stm32_link.py` | STM32 UART. Pi→STM32 `[0xAA][pan][tilt][trig][chk]`, STM32→Pi `[0x55][pan][tilt][chk]`. |
| `lidar.py` | `TFLuna` 9바이트 프레임 파서. `set_trigger_mode`/`measure_once_mm`. |
| `motor_hat.py` | `MotorHat`(PCA9685 0x6F) DC+서보. 라이브러리 없으면 mock. **fwd/back 매핑 swap됨(배선 역극성 보정)**. |
| `*_test.py`, `servo_set.py`, `lidar_glare_check.py`, `lidar_cam_test.py` | 단독 점검 도구. |

### 그 외
- `firmware/` — `stm32_main.c`(F401RE, 서보 제어), `esp32_hit/`(명중 판정 ino).
- `train/` — `train_yolo.py`(YOLO 학습, `--workers 0`로 Windows 데드락 회피), `train_angle_mlp.py`.
- `tools/` — `mock_rpi.py`, `collect_dataset.py`(학습 프레임 수집), `calib_fire.py`(각도별 발사+거리 기록), `prepare_ballistic_csv.py`, `check_*.py`.
- `web/` — `index.html`(조준), `dashboard.html`(통계·AI·챗봇), `app.js`, `style.css`. **`app.js`는 `?v=N` 캐시버스팅** — 수정 시 번호 올리기.
- `data/` — `targets/`(YOLO 데이터셋), `0604_v3/`(배포 모델 학습셋), `feedback/`(런타임: calibration.json·shots.csv·shots.db).
- `models/yolo/best.pt` — 배포 YOLO 가중치(서버 자동 로드). `models/angle_mlp.pt` — 미사용.

---

## 3. 발사 시퀀스 (상태머신, `engine._run`)

```
AIMING  : pan P제어로 표적을 화면 중앙(좌우)에 정렬. (tilt는 안 씀, 아래 §5)
  ↓ 중앙 정렬 완료
[조준 정착 대기 post_aim_delay_s(3s)]   ← "조준 따로/발사 따로" 누르던 효과 자동화
RANGING : RANGE_REQ 전송 → LiDAR 1회 측거 (이때만 IR ON; 트리거 모드)
FIRING  : _predict_angles로 발사각 산출 → 서보 이동 → [fire_settle_s(1s)] → trigger=1
JUDGING : hit_wait_seconds(10s) 동안 ESP32 명중 신호 대기 (1초 카운트다운 메시지)
RESULT  : HIT/MISS 확정 + DB 로그 적재
_recenter : 발사 후 recenter_delay_s(1s) 뒤 pan을 정면(pan_home)으로 복귀
```
실물 없이도 `tools/mock_rpi.py`(STATUS/LIDAR 응답) + `DETECTOR=demo`면 전체 흐름이 돈다.

---

## 4. 발사각 계산 — `_predict_angles` (★ 핵심, MLP 아님)

**우선순위: 실측표(calib_table) > 해석식(use_analytical_angle) > MLP > 폴백.**
현재는 **①실측표**가 동작.

```python
distance_m = LiDAR거리(mm) * 0.001
# 좌우: 조준값 + 고정 오프셋
pan_fire  = clamp(pan_cur + pan_aim_offset_deg(-8°),  0~180)
# 상하: 실측표 보간 (solve_angle_table)
model_angle = 거리→각도 선형보간   # 각도 오름차순, 거리 처음 가로지르는 구간(저탄도 우선)
used_angle  = model_angle + tilt_bias(피드백)        # 표 경로는 시차/오프셋 안 더함
tilt_servo  = clamp(round(used_angle),  0~30°)
```
- 실측표 `calib_table = ((0,1.3),(10,1.8),(20,2.1),(30,2.0))` (각도°, 낙하거리 m).
  **비단조**(20°가 정점 2.1m, 30°는 2.0m로 하강) → `solve_angle_table`은 각도순으로 훑어 **가장 낮은 각** 선택, 정점 초과 거리는 정점각 고정.
- 표를 비우면 `solve_angle`(용수철+선형공기저항 닫힌형 역산) 사용. 그것도 끄면 MLP.
- `state.last_shot`에 발사 정보 기록(피드백·로그용).

---

## 5. 조준 로직 (`controller.py`)

- **pan(좌우)만으로 중앙 정렬 판정** (`aim_use_tilt=False`): 카메라를 위로 못 드는 기구라 세로는 무시, tilt는 발사 시 탄도로만 정함.
- **거리 적응 허용오차**: `tol = clamp(박스폭 × center_tolerance_frac, center_tol_min_px, center_tolerance_px)` = 표적 폭의 일정 비율 → 멀수록(박스 작음) 엄격. 현재 `frac=0.12, min=25, cap=30`.
- P제어: `pan_delta = err.dx × gain(0.016)`, `invert_pan`로 부호, `max_step_deg(2.5)` 클램프.
- **레일 박힘 안전장치**: pan이 0/180에 박혔는데 정렬 안 되면 ~2초 후 "조준 한계"로 포기.

---

## 6. 통신 프로토콜

### PC ↔ Pi (TCP 9000) — `[1B type][4B len(BE)][payload]`
| type | MsgType | 방향 | payload |
|:-:|---|---|---|
|1|FRAME|Pi→PC|JPEG|
|2|STATUS|Pi→PC|`<ii`(pan,tilt)|
|3|LIDAR|Pi→PC|`<i`(mm)|
|4|CONTROL|PC→Pi|`<iii`(pan,tilt,trigger)|
|5|RANGE_REQ|PC→Pi|없음|
|6|MOTOR|PC→Pi|JSON(HAT)|

### Pi ↔ STM32 (UART USART1, 115200 8N1)
```
Pi→STM32 (5B): [0xAA][pan 0-180][tilt 0-180][trigger 0/1][chk=(pan+tilt+trig)&0xFF]
STM32→Pi (4B): [0x55][pan_cur][tilt_cur][chk=(pan+tilt)&0xFF]
```
**각도는 1바이트 정수(0~180)** → 서보 제어 최소단위 1°.

### Pi ↔ LiDAR (TF-Luna, UART via CP2102 115200): 9바이트 `[0x59][0x59][DistL][DistH][AmpL][AmpH][TempL][TempH][chk]`
### ESP32 → PC (WiFi): `POST /api/hit  {source, ms, value(피에조 0~4095)}`

---

## 7. 핀맵 (Nucleo-F401RE)
- **USART1**: PA9(TX,D8)→Pi GPIO15(RX,핀10), PA10(RX,D2)←Pi GPIO14(TX,핀8), GND 공통. (USART2는 ST-Link VCP 충돌 → USART1)
- **TIM3 PWM(50Hz)**: PA6=Pan(CH1), PA7=Tilt(CH2), PB0=Trigger(CH3). 펄스 0.5~2.5ms→0~180°. Trigger: 평상 120°/발사 0°/500ms 후 자동복귀.
- Pi UART=`/dev/serial0`, LiDAR=`/dev/ttyUSB0`. Motor HAT I2C=`0x6F`(DC ch2, 서보 ch0, 60Hz). 서보 전원 외부 5~6V, GND 공통.

---

## 8. config 레퍼런스 (`app/config.py`, 현재 값)
| 그룹 | 키 | 값 | 의미 |
|------|-----|-----|------|
| Aiming | center_tolerance_px / frac / min | 30 / 0.12 / 25 | 적응 허용오차 상한/비율/하한 |
| | invert_pan / invert_tilt | True / True | 서보 방향 반전 |
| | aim_use_tilt | False | 세로 정렬 안 씀(pan만) |
| | tilt_min/max_deg | 0 / 30 | 발사 tilt 범위 |
| | pan_gain_deg_per_px / max_step_deg | 0.016 / 2.5 | P제어 |
| Fire | hit_wait_seconds | 10 | 명중 대기(카운트다운) |
| | post_aim_delay_s | 3.0 | 조준완료→측거 전 정착 |
| | fire_settle_s | 1.0 | 발사각 이동→trigger 전 정착 |
| | recenter_after_fire / recenter_delay_s | True / 1.0 | 발사 후 정면복귀 |
| | pan_home_deg / tilt_home_deg | 90 / 10 | 대포 초기화·복귀 자세 |
| Ballistic | calib_table | ((0,1.3),(10,1.8),(20,2.1),(30,2.0)) | 실측 거리표 |
| | pan_aim_offset_deg | -8.0 | 발사 pan 고정 보정(음수=오른쪽) |
| | use_analytical_angle | True | 표 없을 때 물리식 |
| Ai | base_url/model | gms.ssafy.io.../api.anthropic.com, claude-opus-4-8 | 키=env `GMS_KEY` |

> ⚠️ `pan_bias_deg(-6)`, `pan_offset_per_m`는 **현재 미사용**(엔진이 안 읽음). 발사 pan 보정은 `pan_aim_offset_deg`만.

---

## 9. API 엔드포인트 (`main.py`)
`GET /` `/dashboard` `/video_feed`(MJPEG) `/api/snapshot`(원본) `/api/state` `/api/shots` `/api/stats`
`WS /ws`(10Hz 상태)
`POST /api/select/{id}` `/api/select_at` `/api/clear_selection` `/api/clear_result`
`/api/engage`(발사시퀀스, `{aim_only}`) `/api/hit`(ESP32) `/api/home`(대포초기화) `/api/test_fire`(캘리브용 생발사)
`/api/motor/dc` `/api/motor/servo` `/api/feedback` `/api/feedback/reset` `/api/shots/clear`
`/api/ai_report` `/api/ai_chat`

---

## 10. 실행 / 테스트
```powershell
# PC 서버
.\run_server.ps1          # 또는 run_server.bat (= uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2)
#   DETECTOR=demo 면 GPU/가중치 없이 흐름 테스트
```
```bash
# Pi 노드
cd ~/pi && source venv/bin/activate
python3 rpi_node.py --pc-host <PC_IP> --stm32-port /dev/serial0 --lidar-port /dev/ttyUSB0
# 로컬 통합 테스트(PC, 실물 없이): python -m tools.mock_rpi --mode synthetic --auto-hit
# 단위: python -m tests.test_{protocol,aiming,feedback,pi_protocol_compat,stm32_link,lidar}
```

---

## 11. 주요 설계 결정 (왜)
- **YOLO는 PC GPU**: Pi5는 GPU 없어 1280에서 ~1fps. 엣지=센싱, 서버=추론.
- **LiDAR 트리거 모드**: noir 카메라가 IR 반사광 받아 중앙서 미검출 → 측거 시에만 ON.
- **수신/검출 스레드 분리**: TCP 수신이 YOLO에 막히면 영상 끊김 → 검출은 워커, 최신 프레임만.
- **위치 기반 선택 락 + 자동 재선택**: 서보 회전으로 ByteTrack id 흔들림 → 마지막 위치 최근접으로 재바인딩, 잠깐 놓쳐도 복구.
- **발사각=실측표 보간**: 측정점 적을 땐 MLP보다 정확(MLP는 근거리 -60° 헛값). 표 비우면 해석식/MLP 폴백.
- **조준 후 정착 딜레이**: 조준·발사를 따로 누를 때 명중률 높았던 걸 자동화.

---

## 12. 규약 & 함정 (AI 어시스턴트 필독)
- **`protocol.py` 이중 사본**: `app/comms/protocol.py`와 `pi/protocol.py`는 동일해야 함. 한쪽 고치면 둘 다. (`tests/test_pi_protocol_compat.py`로 방어)
- **서버 코드 변경 → 서버 재시작 필요**. config/engine/main 등 모두.
- **웹 파일 변경 → 브라우저 새로고침**. `index.html`은 `app.js?v=N` 캐시버스팅 — JS 고치면 `v` 번호 올릴 것. 안 그러면 옛 JS 캐시로 핸들러 미반영.
- **Pi 파일(`pi/`) 변경 → Pi로 복사 후 `rpi_node` 재시작**. (SSH: 과거 `172.20.10.6`, id `pi`, paramiko로 SFTP)
- **GMS_KEY는 코드에 없음** — env 전용. 레포에 키 없음(공개 안전).
- **calib_table이 비면** 발사각이 해석식/MLP로 폴백 — 의도치 않게 비우지 말 것.
- **각도 단위**: UART가 1바이트 정수라 서보 1° 단위. 소수점 제어 불가(프로토콜·펌웨어 수정 필요).
- 모터 fwd/back: `motor_hat.py`에서 배선 역극성 보정으로 매핑 swap됨.

---

## 13. 환경 / 트러블슈팅
- **PyTorch**: GPU 빌드 먼저. RTX40→cu124, RTX50(Blackwell)→cu128, CPU→cpu. (설치됨: torch 2.6.0+cu124, ultralytics 8.4.75, fastapi 0.138, opencv 4.13, numpy 2.4)
- **Pi Bus error**: pip로 opencv/numpy 설치 금지 → apt(`python3-opencv/numpy/picamera2/serial`) + venv `--system-site-packages`.
- **picamera2 색 반전**: 'RGB888'이 실제 BGR → 색변환 안 함.
- **YOLO 학습 Windows 데드락**: `train_yolo.py --workers 0`.
- **방화벽**: 8000(웹·ESP32)·9000(Pi TCP) 허용. 같은 네트워크(핫스팟). PC IP 바뀌면 Pi `--pc-host` 갱신.
- **성능**: YOLO 추론 ~69fps(RTX4050,1280), 명중률 61/100@1~2m, 사거리 1.3~2.1m, LiDAR ±6cm.
