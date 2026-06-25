"""시스템 전역 설정.

경로, 네트워크 포트, 조준/발사 파라미터, YOLO 추론 옵션을 한곳에서 관리한다.
환경변수로 일부 값을 덮어쓸 수 있다 (예: SERVER_HOST, RPI_TCP_PORT).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- 경로 ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # D:\server
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"

YOLO_WEIGHTS = MODELS_DIR / "yolo" / "best.pt"   # 커스텀 표적 학습 산출물
ANGLE_MODEL = MODELS_DIR / "angle_mlp.pt"        # MLP 각도 회귀 모델

ANGLE_CSV_DIR = DATA_DIR / "angle"               # PyBullet 학습 CSV
TARGETS_DATA_YAML = DATA_DIR / "targets" / "data.yaml"  # YOLO 데이터셋 정의

FEEDBACK_DIR = DATA_DIR / "feedback"             # 실측 발사 로그/보정값
SHOTS_CSV = FEEDBACK_DIR / "shots.csv"           # 실측 발사 기록(재학습용)
CALIBRATION_JSON = FEEDBACK_DIR / "calibration.json"  # tilt_bias 영속
SHOTS_DB = FEEDBACK_DIR / "shots.db"             # 발사+명중판정 로그(SQLite, 시각화용)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class NetworkConfig:
    # FastAPI / 웹 UI
    http_host: str = _env("SERVER_HOST", "0.0.0.0")
    http_port: int = int(_env("SERVER_PORT", "8000"))
    # Raspberry Pi 와의 TCP (PC 가 서버로 listen, RPi 가 접속)
    rpi_tcp_host: str = _env("RPI_TCP_HOST", "0.0.0.0")
    rpi_tcp_port: int = int(_env("RPI_TCP_PORT", "9000"))


@dataclass
class AimingConfig:
    """조준 루프 파라미터.

    객체 중심과 화면 중앙의 픽셀 오차를 각도 보정량으로 환산한다.
    gain 은 '픽셀당 도(degree)' 단위의 비례 계수다.
    """
    center_tolerance_px: int = 30      # '중앙 정렬' 허용 픽셀(상한). 가까운(큰 박스) 표적의 정밀도 — ↓일수록 빡빡
    # 거리 적응 정렬: >0 이면 박스폭 비례로 허용오차를 정한다(멀수록 박스 작음 → 더 엄격).
    #   tol = clamp(박스폭 × center_tolerance_frac, center_tol_min_px, center_tolerance_px)
    #   = "표적 폭의 ±frac 이내로 조준" → 거리와 무관하게 실제 명중 정밀도 일정. 0 이면 고정 px 사용.
    center_tolerance_frac: float = 0.12
    center_tol_min_px: int = 25        # 적응 모드 하한(먼 표적/서보 분해능 바닥). 너무 작으면 조준 안 끝남
    pan_gain_deg_per_px: float = 0.016 # 좌우 오차 → pan 보정 비례계수(↓ → 오버슈트/진동/모션블러 억제)
    tilt_gain_deg_per_px: float = 0.016 # 상하 오차 → tilt 보정 비례계수
    max_step_deg: float = 2.5          # 한 스텝당 최대 각도 변화(작게 → 블러↓ 검출 유지, 도달 약간 느려짐)
    pan_min_deg: int = 0
    pan_max_deg: int = 180
    tilt_min_deg: int = 0
    tilt_max_deg: int = 30    # 대포 tilt 가동/발사 범위: 0°(수평) ~ 30°(최대 앙각, 실측 기준)
    # tilt 화면 좌표계: 영상에서 y 가 커지면(아래로) tilt 를 줄여야 하는지 여부.
    invert_tilt: bool = True
    # pan 방향: 서보 회전 방향이 화면 좌우와 반대면 True (좌우 트래킹이 반대로 돌 때).
    invert_pan: bool = True
    # 조준 정렬/제어에 tilt(세로)를 쓸지 여부.
    #   False(기본): 카메라를 위로 못 드는 기구이거나, 발사 tilt 를 탄도(MLP)로만
    #     정하는 경우 -> 조준은 pan(좌우)만으로 정렬 판정하고 tilt 는 건드리지 않는다.
    #   True: 짐벌이 상하로 자유롭고 세로 정렬까지 맞추고 싶을 때.
    aim_use_tilt: bool = False


@dataclass
class FireConfig:
    hit_wait_seconds: float = 10.0     # 발사 후 명중 신호 대기 시간
    trigger_release_value: int = 1     # ControlPacket.trigger 발사 값
    # 조준 완료 후, 발사 시퀀스(측거·발사)로 넘어가기 전 정착 대기(초).
    #   '조준만' 누르고 따로 '발사'를 누르던 두 단계처럼, 조준 자세에서 한 번 안정시킨다.
    post_aim_delay_s: float = 3.0
    # 발사 각도로 이동한 뒤 트리거 전까지의 정착 대기(초). 이동 흔들림이 멎을 텀.
    fire_settle_s: float = 1.0
    # 발사 후 포탑(pan)을 차체 정면(중립)으로 복귀시켜 '차체 방향 = 카메라 방향' 정렬.
    # (자동차 조향 차체는 제자리 회전이 안 되므로, 차체 대신 포탑을 정면으로 되돌린다)
    recenter_after_fire: bool = True
    recenter_delay_s: float = 1.0      # 판정 후 정면 복귀까지 대기(초). 발사 여운 두고 정렬
    pan_home_deg: int = 90             # 차체 정면에 해당하는 pan 서보각(중립). 대포 초기화에도 사용
    tilt_home_deg: int = 10            # 대포 초기 tilt(10°). 초기화 자세 = pan 90 / tilt 10 / trigger 0


@dataclass
class BallisticConfig:
    """발사각(tilt) 탄도 보정 설정.

    조준 단계에서 pan/tilt 로 표적을 화면 중앙에 맞춰 LiDAR 로 거리를 잰 뒤,
    pan 은 조준값을 유지하고 tilt 만 거리 기반 발사각(MLP 출력 'angle')으로 바꿔 발사한다.

    MLP 입력: (weight, air_resistance, landing_distance[m]) -> 출력: angle[deg]
    실제 발사체 스펙(weight/air_resistance)은 여기 고정값으로 두고 추론 시 사용한다.
    """
    # 발사체 고정 스펙 (CSV 의 조합 중 하나; 실측값으로 조정)
    projectile_weight: float = float(_env("PROJ_WEIGHT", "0.003"))       # kg
    projectile_air_resistance: float = float(_env("PROJ_AIR", "0.05"))   # kg/s

    # tilt 서보 각도 매핑: 발사각(수평 대비 앙각)을 tilt 서보 각도로 변환
    #   tilt_servo = tilt_horizontal_deg + up_sign * launch_angle_deg
    tilt_horizontal_deg: int = 0    # tilt 서보가 '수평'일 때의 각도(0°). 발사각 = 0 + 발사각도
    tilt_up_sign: int = 1           # 앙각 증가 시 서보각 증가(+1) / 감소(-1)

    # LiDAR 거리 단위 변환 (mm -> m). CSV 의 landing_distance 는 m 단위.
    mm_to_m: float = 0.001

    # 발사구(대포)가 카메라보다 위에 있는 높이[m]. 카메라로 표적을 조준해도 발사는
    # 이만큼 위에서 나가므로, 거리 기반 시차 보정(atan(offset/distance))으로 tilt 를 낮춘다.
    # 발사구가 위 -> 표적이 발사구 기준 더 아래 -> 발사각 ↓. (부호가 반대면 음수로)
    launcher_above_camera_m: float = 0.10

    # 발사 좌우 보정[deg]: 최종 pan 보정 = pan_bias_deg + pan_offset_per_m * 거리[m]
    #   pan_bias_deg     : 거리 무관 고정 성분(절편)
    #   pan_offset_per_m : 거리 1m 당 추가 성분(기울기) — 거리마다 치우침이 다를 때
    pan_bias_deg: float = -6.0
    pan_offset_per_m: float = 0.0

    # 발사 좌우 조준 오프셋[deg]: 발사 pan 에 더하는 고정 보정(거리 무관).
    #   이 장치 확인 결과: 음수 = 오른쪽, 양수 = 왼쪽. 왼쪽 빗나감 보정은 음수(-).
    pan_aim_offset_deg: float = float(_env("PAN_AIM_OFFSET", "-8.0"))

    # --- 피드백 보정 (sim-to-real) ---
    bias_lr: float = 0.5        # 잔차 보정 학습률(지수이동평균 누적)
    bias_max_deg: float = 20.0  # tilt_bias 클램프 범위(±도)
    # cm->deg 폴백 계수: predictor 가 없을 때만 사용(앞이면 거리↑ 위해 음의 부호)
    fallback_deg_per_cm: float = 0.2

    # --- 실측 캘리브레이션 표 (거리 -> 발사각, 최우선) ---
    # (tilt_각도°, 실측 낙하거리 m). 비어있지 않으면 이 표를 보간해 각도를 정한다.
    # 물리식/MLP 추정보다 실제 장치에 정확하다. tools/calib_fire.py 로 측정해 채운다.
    calib_table: tuple = ((0, 1.30), (10, 1.80), (20, 2.10), (30, 2.00))

    # --- 해석식 탄도 역산 (거리 -> 발사각). calib_table 이 비었을 때 사용 ---
    # True 면 MLP 대신 닫힌형 물리식(260610.ipynb)으로 발사각을 계산한다.
    # 학습 범위가 없어 근거리 외삽 헛값(예: -60°)이 안 나오고, 도달 불가 거리를 안전 처리.
    use_analytical_angle: bool = True
    spring_k: float = float(_env("SPRING_K", "320.0"))    # 용수철 탄성계수 N/m (실측 보정)
    spring_x: float = float(_env("SPRING_X", "0.05"))     # 용수철 압축거리 m
    launch_height_m: float = float(_env("LAUNCH_H", "1.0"))  # 발사 높이(착탄면 기준) m
    gravity: float = 9.81

    # 좌우(pan) 피드백 보정: 착탄이 좌/우로 빗나간 cm 를 각도로 환산해 pan_bias 누적.
    #   발사 pan = pan_cur + pan_bias_deg + pan_offset_per_m*거리 + (학습된)pan_bias
    pan_bias_lr: float = 0.5            # pan 잔차 보정 학습률(EMA)
    pan_bias_max_deg: float = 20.0     # pan_bias 클램프(±도)
    pan_fallback_deg_per_cm: float = 0.2  # 거리 모를 때 cm->deg 폴백
    # 우측(+)으로 빗나가면 pan_bias 를 어느 방향으로 밀지. 보정이 반대로 악화되면 -1 로.
    pan_feedback_sign: float = 1.0


@dataclass
class YoloConfig:
    device: str = _env("YOLO_DEVICE", "0")   # "0" = 첫 GPU, "cpu" 가능
    conf_threshold: float = float(_env("YOLO_CONF", "0.20"))  # ↓: 멀어서 작아진 표적도 검출
    iou_threshold: float = 0.45
    imgsz: int = 1280   # ↑: 작은(먼) 표적 디테일 보존(GPU면 충분히 빠름)
    # 가중치가 없을 때 폴백으로 쓸 사전학습 모델(개발/데모용)
    fallback_weights: str = "yolov8n.pt"


@dataclass
class ScoreConfig:
    """피에조 센서 세기 → 점수 환산.

    ESP32 가 보낸 충격 피크값(0~4095, ADC 12bit)을 점수로 매핑한다.
    sensor_min(=ESP32 HIT_THRESHOLD 수준) 이하는 0점, sensor_max 이상은 만점.
    그 사이는 선형 비례. 등급(grade)은 점수 구간으로 매긴다.
    """
    sensor_min: float = float(_env("SCORE_MIN", "600"))    # 이 세기 이하 = 0점(약한 스침)
    sensor_max: float = float(_env("SCORE_MAX", "3500"))   # 이 세기 이상 = 만점(센서 포화 고려)
    max_score: int = int(_env("SCORE_MAX_POINTS", "100"))  # 만점


@dataclass
class AiConfig:
    """발사 로그 AI 분석(Claude via SSAFY GMS 프록시).

    키는 코드에 넣지 않고 환경변수(GMS_KEY)로만 읽는다. base_url 뒤에 SDK 가
    /v1/messages 를 붙인다. 진짜 Anthropic API 와 요청 형식·모델명이 동일하다.
    """
    base_url: str = _env("GMS_BASE_URL", "https://gms.ssafy.io/gmsapi/api.anthropic.com")
    api_key_env: str = "GMS_KEY"             # 키가 담긴 환경변수 이름
    model: str = _env("GMS_MODEL", "claude-opus-4-8")
    max_tokens: int = int(_env("GMS_MAX_TOKENS", "2000"))


@dataclass
class Config:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    aiming: AimingConfig = field(default_factory=AimingConfig)
    fire: FireConfig = field(default_factory=FireConfig)
    ballistic: BallisticConfig = field(default_factory=BallisticConfig)
    yolo: YoloConfig = field(default_factory=YoloConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    ai: AiConfig = field(default_factory=AiConfig)


# 전역 단일 설정 인스턴스
config = Config()
