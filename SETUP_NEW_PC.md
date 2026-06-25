# 새 PC 셋업 가이드 (서버 이전/구축)

PC 서버를 새 컴퓨터에서 구동하기 위한 단계별 가이드.
(Raspberry Pi·STM32·ESP32 하드웨어는 그대로 사용 — PC만 새로 세팅한다.)

---

## 0. 새 PC로 가져올 것
- [ ] **프로젝트 폴더 전체** (`server\`) — `.venv`, `__pycache__`, `data\feedback\` 는 빼도 됨
- [ ] **모델 파일** (필수, 빠지기 쉬움):
  - [ ] `models\yolo\best.pt` (YOLO 표적 검출)
  - [ ] `models\angle_mlp.pt` (발사각 MLP)
- [ ] (선택) 재학습용 데이터: `data\angle\*.csv`, `data\targets\`
- [ ] (선택) Claude 맥락: `C:\Users\<사용자>\.claude\projects\D--server\memory\`

> 옮기는 방법: USB/외장하드/클라우드로 폴더 복사, 또는 Git(단 best.pt는 .gitignore면 따로 챙김).

---

## 1. 사전 설치 (새 PC)
- [ ] **Python 3.12** (또는 3.11) 설치 — "Add to PATH" 체크
- [ ] **NVIDIA 그래픽 드라이버** 최신 (GPU가 5060 Ti면 Blackwell 지원 드라이버)
- [ ] (선택) Git

---

## 2. 프로젝트 배치
```powershell
# 가져온 폴더를 원하는 경로에 둔다 (예: D:\server)
cd D:\server
```

---

## 3. 가상환경 + PyTorch + 의존성

```powershell
# (1) 가상환경
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip

# (2) PyTorch — GPU에 맞는 CUDA 빌드로 "먼저" 설치
#     RTX 50 시리즈(5060 Ti, Blackwell):
.\.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
#     RTX 40 시리즈(4060)면 cu128 대신 cu124
#     GPU 없이 CPU만이면 .../whl/cpu

# (3) 나머지 의존성
.\.venv\Scripts\pip install -r requirements.txt
```

> ⚠️ torch를 **먼저** cu 빌드로 깔아야 ultralytics가 torch를 재설치하지 않는다.
> ⚠️ C: 용량 부족하면 임시폴더를 D:로: `$env:TMP="D:\tmp"; $env:TEMP="D:\tmp"`

---

## 4. GPU / 모델 확인
```powershell
# GPU 인식 (arch_list 에 sm_120, 이름 RTX 5060 Ti 확인)
.\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0)); print(torch.cuda.get_arch_list())"

# 모델 파일 존재 확인
dir models\yolo\best.pt
dir models\angle_mlp.pt
```
- `cuda.is_available() == True` + 이름이 GPU와 일치하면 OK
- 5060 Ti인데 `False`거나 `sm_120` 없으면 → torch가 cu128이 아님(재설치)

---

## 5. 서버 실행
```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2
```
- 로그에 `YOLO 모델 로드: ...best.pt (device=0)` + `RPi TCP 서버 listen: 0.0.0.0:9000` 뜨면 정상
- 브라우저: `http://<이 PC IP>:8000`
- (실물 없이 흐름만 테스트하려면 환경변수 `DETECTOR=demo` 로 실행)

---

## 6. 방화벽 (관리자 PowerShell, 최초 1회)
```powershell
New-NetFirewallRule -DisplayName "aim-server" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000,9000
```

---

## 7. Pi 연결 (PC IP가 바뀌므로 갱신)
새 PC의 IP 확인:
```powershell
ipconfig    # IPv4 주소 확인 (예: 192.168.x.x)
```
Pi에서 실행 시 `--pc-host`를 **새 PC IP**로:
```bash
cd ~/pi && source venv/bin/activate
python3 rpi_node.py --pc-host <새 PC IP> --stm32-port /dev/serial0 --lidar-port /dev/ttyUSB0
```
- PC와 Pi가 **같은 네트워크**(공유기/핫스팟)에 있어야 함
- STM32는 전원만 (펌웨어 그대로). LiDAR 포트 안 보이면 `ls /dev/ttyUSB*`

---

## 8. 동작 테스트
```powershell
# (A) 로컬 데모(실물 없이): 서버를 DETECTOR=demo 로 띄우고 다른 창에서
.\.venv\Scripts\python -m tools.mock_rpi --mode synthetic --port 9000 --http http://127.0.0.1:8000 --auto-hit

# (B) 단위/통합 테스트
.\.venv\Scripts\python -m tests.test_protocol
.\.venv\Scripts\python -m tests.test_aiming
.\.venv\Scripts\python -m tests.test_feedback
```

---

## ✅ 최종 체크리스트
- [ ] 프로젝트 폴더 + `best.pt` + `angle_mlp.pt` 복사됨
- [ ] Python venv 생성
- [ ] torch가 **GPU 맞는 cu 빌드** (5060 Ti=cu128) + `cuda.is_available()==True`
- [ ] `pip install -r requirements.txt`
- [ ] 서버 기동 → `best.pt` 로드 + listen 로그 확인
- [ ] 방화벽 8000/9000 허용
- [ ] Pi `--pc-host`를 새 IP로, 같은 네트워크
- [ ] 브라우저에서 영상·표적 박스 확인

---

## 트러블슈팅 (요약)
| 증상 | 해결 |
|------|------|
| `cuda.is_available()` False (5060 Ti) | torch를 **cu128**로 재설치 |
| 서버가 Ctrl+C로 안 꺼짐 | `--timeout-graceful-shutdown 2` / 터미널 창 닫기 |
| Pi 연결 안 됨 | `--pc-host` IP, 같은 네트워크, 방화벽 8000/9000 |
| best.pt 못 찾음 | `models\yolo\best.pt` 위치 확인 (없으면 재학습) |
| C: 디스크 부족 | `$env:TMP/$env:TEMP="D:\tmp"` |

> 더 자세한 설계·실행은 `CLAUDE.md`, 하드웨어/펌웨어는 `firmware/README.md`, Pi 설정은 `pi/README.md` 참고.
