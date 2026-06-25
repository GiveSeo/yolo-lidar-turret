"""자동 조준 컨트롤러.

선택된 객체의 중심과 화면 중앙의 픽셀 오차를 계산하여, 현재 서보 각도에
더할 pan/tilt 보정량을 산출한다. 비례(P) 제어이며 스텝당 변화량을 제한한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import config


@dataclass
class AimError:
    dx: float          # 가로 오차(px): 객체중심 - 화면중심 (오른쪽이 +)
    dy: float          # 세로 오차(px): 객체중심 - 화면중심 (아래쪽이 +)
    centered: bool     # 허용 오차 이내 정렬 여부


def compute_error(
    target_cx: float,
    target_cy: float,
    frame_w: int,
    frame_h: int,
    box_w: float | None = None,
) -> AimError:
    """객체 중심과 화면 중앙의 오차를 계산한다.

    box_w(표적 박스 폭)가 주어지고 center_tolerance_frac>0 이면, 허용오차를 박스폭에
    비례시켜 '거리 적응'으로 만든다(멀수록 박스 작음 → tol 작음 → 더 엄격).
    """
    a = config.aiming
    cx = frame_w / 2.0
    cy = frame_h / 2.0
    dx = target_cx - cx
    dy = target_cy - cy

    tol = a.center_tolerance_px
    if a.center_tolerance_frac > 0 and box_w:
        tol = max(a.center_tol_min_px,
                  min(a.center_tolerance_px, box_w * a.center_tolerance_frac))

    # tilt 를 안 쓰는 기구면 세로(dy)는 무시하고 좌우(dx)만으로 정렬을 판정한다.
    # (발사 tilt 는 탄도 MLP 가 거리로 정하므로 세로 정렬은 발사에 영향 없음)
    if a.aim_use_tilt:
        centered = abs(dx) <= tol and abs(dy) <= tol
    else:
        centered = abs(dx) <= tol
    return AimError(dx=dx, dy=dy, centered=centered)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def next_aim_angles(
    err: AimError,
    pan_current: int,
    tilt_current: int,
) -> tuple[int, int]:
    """오차와 현재 각도로부터 다음 목표 pan/tilt 각도를 계산한다.

    카메라가 pan/tilt 짐벌 위에 있다고 가정한다:
      - 객체가 화면 오른쪽(dx>0)이면 pan 을 늘려 오른쪽을 향하게 한다.
      - 객체가 화면 아래(dy>0)면 (invert_tilt=True) tilt 를 줄여 아래를 향하게 한다.
    실제 기구 방향에 따라 config 의 gain 부호/ invert_tilt 로 보정한다.
    """
    a = config.aiming
    pan_delta = err.dx * a.pan_gain_deg_per_px
    # tilt 를 안 쓰는 기구면 세로 보정을 0 으로 둬 tilt 서보를 건드리지 않는다(현재값 유지).
    tilt_delta = err.dy * a.tilt_gain_deg_per_px if a.aim_use_tilt else 0.0
    if a.invert_pan:
        pan_delta = -pan_delta
    if a.invert_tilt:
        tilt_delta = -tilt_delta

    # 스텝당 최대 변화량 제한 (오버슈트/진동 방지)
    pan_delta = _clamp(pan_delta, -a.max_step_deg, a.max_step_deg)
    tilt_delta = _clamp(tilt_delta, -a.max_step_deg, a.max_step_deg)

    pan_target = _clamp(round(pan_current + pan_delta), a.pan_min_deg, a.pan_max_deg)
    tilt_target = _clamp(round(tilt_current + tilt_delta), a.tilt_min_deg, a.tilt_max_deg)
    return int(pan_target), int(tilt_target)
