# Raspberry Pi 5 통신 노드

PC 서버와 STM32·TF-Luna·카메라를 잇는 중계 노드. 이 `pi/` 폴더를 Pi 로 복사해 실행한다.

## 구성

```
pi/
  rpi_node.py    메인: 카메라/LiDAR/STM32/PC TCP 통합 (스레드 + 자동 재연결)
  protocol.py    PC<->Pi TCP 프로토콜 (서버 app/comms/protocol.py 와 동일)
  stm32_link.py  STM32 UART 링크 (5B 명령 / 4B 상태)
  lidar.py       TF-Luna 9바이트 프레임 파서 (cm->mm)
  requirements.txt
```

## 데이터 흐름

```
PiCamera ──FRAME──▶ PC          (YOLO 검출)
TF-Luna  ──LIDAR──▶ PC          (거리 mm)
STM32    ──STATUS─▶ PC          (현재 pan/tilt)
PC ──CONTROL──▶ Pi ──5B 명령──▶ STM32   (pan/tilt/trigger)
```

## 설치

```bash
# ① opencv·numpy·picamera2·pyserial 은 시스템 패키지(apt)로 설치 — pip 로 깔지 말 것!
sudo apt install -y python3-picamera2 python3-opencv python3-numpy python3-serial

# ② venv 는 반드시 --system-site-packages 로 생성(시스템 패키지 상속)
python3 -m venv --system-site-packages venv
source venv/bin/activate                        # 활성화 (프롬프트에 (venv) 표시)

# ③ pyserial 만 (위 apt 로 python3-serial 깔았으면 생략 가능)
pip install -r requirements.txt

# 이후 rpi_node.py 실행도 반드시 venv 활성화 상태에서.
# (새 터미널/재부팅 후엔 cd ~/pi 후 source venv/bin/activate 다시)
```

> ⚠️ **Bus error 주의**: `pip install opencv-python` 또는 `pip install numpy` 를 하면
> picamera2 가 기대하는 시스템 numpy 와 ABI 가 충돌해 **Bus error/segfault** 가 난다.
> Pi 에서는 opencv·numpy 를 **반드시 apt(python3-opencv, python3-numpy)** 로 쓴다.
> 이미 pip 로 깔았다면: `pip uninstall -y opencv-python numpy` 로 제거 후 위 apt 설치.

## 포트 구성 (확정: A안)

- **STM32** → Pi GPIO UART `/dev/ttyAMA0` (GPIO14 TXD pin8 / GPIO15 RXD pin10)
- **TF-Luna** → **CP2102 USB-시리얼 어댑터** → Pi USB `/dev/ttyUSB0`

GPIO UART 활성화 (STM32용):
- `sudo raspi-config` → Interface Options → Serial Port
  → 로그인 셸 **No**, 시리얼 하드웨어 **Yes** → 재부팅
- CP2102 는 꽂으면 `cp210x` 드라이버로 자동 인식. `ls /dev/ttyUSB*` 로 확인
  (보통 `/dev/ttyUSB0`). STM32 포트는 `ls /dev/ttyAMA*` 로 확인.

## 배선

**① STM32 ↔ Pi GPIO UART** (둘 다 3.3V TTL → 직결, GND 공통)

| STM32 | 방향 | Pi |
|-------|------|----|
| PA9 (USART1_TX, D8) | → | GPIO15 RXD (pin 10) |
| PA10 (USART1_RX, D2) | ← | GPIO14 TXD (pin 8) |
| GND | ↔ | GND (pin 6/9/14) |

**② TF-Luna ↔ CP2102 ↔ Pi USB**  (TF-Luna 4핀 커넥터 기준)

| TF-Luna | 방향 | CP2102 |
|---------|------|--------|
| pin1 +5V | ← | 5V (VCC out) |
| pin2 RXD | ← | TXD |
| pin3 TXD | → | RXD |
| pin4 GND | ↔ | GND |

- CP2102 → Pi USB 포트에 연결.
- ⚠️ TF-Luna **전원은 5V**, **신호는 3.3V TTL**. CP2102 모듈의 신호 레벨이 3.3V 인지
  확인(대부분 3.3V). 5V 출력 핀으로 TF-Luna 전원을 공급하되, TX/RX 신호선에 5V 가
  실리지 않도록 주의. TF-Luna 가 6핀이면 1~4번만 사용(5·6번은 모드핀, UART 기본 모드면 미연결).
- 카메라는 Pi5 CSI 커넥터에 Pi Camera 연결 (picamera2 사용).

## 실행

```bash
python3 rpi_node.py \
    --pc-host 192.168.0.10 --pc-port 9000 \
    --stm32-port /dev/ttyAMA0 \
    --lidar-port /dev/ttyUSB0 \
    --camera picamera2 --width 1280 --height 720 --fps 20
```

- `--pc-host` 는 PC 서버 IP (PC 가 TCP 서버로 9000 포트 listen).
- TF-Luna 를 mm 출력 포맷으로 설정해 둔 경우 `--lidar-mm` 추가.
- USB 웹캠으로 테스트하려면 `--camera opencv --cam-index 0`.

### 단계별 테스트 모드

```bash
# (1) YOLO 검출만 빠르게 확인 — 카메라만, STM32/LiDAR 불필요
python3 rpi_node.py --pc-host <PC_IP> --no-stm32 --no-lidar \
    --camera picamera2 --width 1280 --height 720

# (2) 조준까지 — 카메라 + STM32 (LiDAR 아직 없을 때)
python3 rpi_node.py --pc-host <PC_IP> --no-lidar \
    --stm32-port /dev/ttyAMA0 --camera picamera2 --width 1280 --height 720

# (3) 전체 — 카메라 + STM32 + LiDAR
python3 rpi_node.py --pc-host <PC_IP> \
    --stm32-port /dev/ttyAMA0 --lidar-port /dev/ttyUSB0 \
    --camera picamera2 --width 1280 --height 720
```

## 동작 확인

1. PC 서버 실행 (`uvicorn app.main:app --host 0.0.0.0 --port 8000`).
2. Pi 에서 위 명령 실행 → PC 웹 UI 의 `RPi` 배지가 켜지고 영상/검출이 표시되면 정상.
3. 브라우저에서 표적 선택 → 발사 시퀀스 시작 → STM32 서보가 조준·발사하는지 확인.
