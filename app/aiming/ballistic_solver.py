"""해석식 탄도 역산 (거리 -> 발사각).

260610.ipynb 의 닫힌형 궤적 모델(용수철 발사 + 선형 공기저항)을 그대로 옮겨,
목표 낙하거리를 내는 발사각을 수치적으로 역산한다. MLP 와 달리 '학습 범위'가
없고, 도달 불가능한 거리(너무 가깝/멀)를 명확히 구분해 안전하게 처리한다.

정방향(각도 -> 거리) 닫힌형:
    v0  = x_spring * sqrt(k_spring / mass)              (용수철 에너지 -> 속도)
    z(x)= h + (tanθ + m g/(c v0 cosθ)) x
            + (m² g / c²) ln(1 - c x /(m v0 cosθ))
    낙하거리 = z(x)=0 의 양의 해 x   (수평 점근선 x_inf = m v0 cosθ / c 미만)
c≈0(무저항)이면 진공 포물선식으로 처리한다.

역방향: [angle_min, angle_max] 에서 정방향(angle)=target 을 만족하는 '가장 낮은'
각도(저탄도)를 찾는다. 도달 불가면 상태(too_far/too_close)와 함께 경계각을 돌려준다.
"""
from __future__ import annotations

import math


def muzzle_velocity(mass: float, k_spring: float, x_spring: float) -> float:
    """용수철 에너지 보존으로 초기 속도 v0 = x*sqrt(k/m)."""
    return x_spring * math.sqrt(k_spring / mass)


def forward_distance(angle_deg: float, mass: float, drag_c: float,
                     k_spring: float, x_spring: float, height: float,
                     g: float = 9.81) -> float:
    """발사각(도) -> 낙하거리(m). 전방으로 못 나가면 0.0."""
    theta = math.radians(angle_deg)
    cos_t = math.cos(theta)
    if cos_t <= 1e-9:
        return 0.0
    v0 = muzzle_velocity(mass, k_spring, x_spring)
    v0x = v0 * cos_t

    # 무저항(진공) 포물선
    if drag_c <= 1e-9:
        v0z = v0 * math.sin(theta)
        t_land = (v0z + math.sqrt(v0z * v0z + 2 * g * height)) / g
        return max(0.0, v0x * t_land)

    # 선형 저항: z(x)=0 의 양의 해를 이분법으로
    x_inf = mass * v0x / drag_c            # 수평 점근선(도달 상한)

    def z_of_x(x: float) -> float:
        inside = 1.0 - (drag_c * x) / (mass * v0x)
        if inside <= 1e-12:
            return -1e18
        return (height
                + (math.tan(theta) + (mass * g) / (drag_c * v0x)) * x
                + ((mass * mass) * g / (drag_c * drag_c)) * math.log(inside))

    lo, hi = 1e-4, x_inf * (1 - 1e-6)
    if z_of_x(lo) <= 0:                    # 시작부터 지면 이하면 비행 없음
        return 0.0
    for _ in range(80):                    # z(lo)>0, z(hi)<0 -> 유일 교차 이분법
        mid = 0.5 * (lo + hi)
        if z_of_x(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def solve_angle(target_distance: float, mass: float, drag_c: float,
                k_spring: float, x_spring: float, height: float,
                angle_min: float = 0.0, angle_max: float = 45.0,
                g: float = 9.81, steps: int = 90) -> tuple[float, str]:
    """목표거리(m) -> 발사각(도). 반환: (angle, status).

    status:
      'ok'        : 범위 내 해를 찾음
      'too_close' : 최소각 도달거리보다 가까움 -> angle_min 반환(가장 평평하게)
      'too_far'   : 최대 도달거리보다 멂      -> angle_max 반환(최대 사거리각)
    저탄도(낮은 각) 해를 우선 선택한다.
    """
    def f(ang: float) -> float:
        return forward_distance(ang, mass, drag_c, k_spring, x_spring, height, g) - target_distance

    f0 = f(angle_min)
    if f0 > 0:                              # 최소각 도달거리 > target -> 더 낮은 각 필요(범위 밖)
        return angle_min, "too_close"

    a0 = angle_min
    step = (angle_max - angle_min) / steps
    for i in range(1, steps + 1):
        a1 = angle_min + i * step
        f1 = f(a1)
        if (f0 <= 0 <= f1) or (f1 <= 0 <= f0):   # 부호 변화 = 근 존재
            lo, hi, flo = a0, a1, f0
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                fm = f(mid)
                if (flo <= 0 <= fm) or (fm <= 0 <= flo):
                    hi = mid
                else:
                    lo, flo = mid, fm
            return 0.5 * (lo + hi), "ok"
        a0, f0 = a1, f1

    return angle_max, "too_far"             # 끝까지 못 미침 = 너무 멀다


def solve_angle_table(target_distance: float, table,
                      angle_min: float = 0.0, angle_max: float = 45.0) -> tuple[float, str]:
    """실측 표((tilt각도, 낙하거리) 들)를 보간해 목표거리 -> 발사각(도).

    거리가 각도에 대해 '오르다 내리는'(정점 있는) 비단조여도 동작한다. 각도 오름차순으로
    구간을 훑어 목표거리를 처음 가로지르는 지점을 쓴다 = 같은 거리를 내는 각 중 '가장
    낮은(저탄도)' 각을 우선 선택. 측정 범위 밖은 경계 측정각으로 고정한다:
      - 최저 측정각의 거리보다 가까움 -> 최저 측정각 (too_close)
      - 어느 구간에도 안 걸림(최대 도달거리=정점보다 멂) -> 정점 각도 (too_far)
    각도는 [angle_min, angle_max] 로 클램프한다.
    """
    pts = sorted(((float(a), float(d)) for a, d in table), key=lambda t: t[0])  # 각도 오름차순
    angs = [a for a, _ in pts]
    dists = [d for _, d in pts]

    def clamp(a: float) -> float:
        return max(angle_min, min(angle_max, a))

    if len(pts) == 1:
        return clamp(angs[0]), "ok"

    # 최저각의 도달거리보다 가까우면 더 낮출 수 없으니 최저각 고정
    if target_distance <= dists[0]:
        return clamp(angs[0]), ("ok" if target_distance == dists[0] else "too_close")

    # 각도 오름차순으로 목표거리를 처음 가로지르는 구간 = 가장 낮은(저탄도) 각도
    for i in range(1, len(pts)):
        d0, d1 = dists[i - 1], dists[i]
        if d1 == d0:
            continue
        lo, hi = (d0, d1) if d0 < d1 else (d1, d0)
        if lo <= target_distance <= hi:
            frac = (target_distance - d0) / (d1 - d0)
            return clamp(angs[i - 1] + frac * (angs[i] - angs[i - 1])), "ok"

    # 어느 구간에도 없음 = 최대 도달거리(정점)보다 멀다 -> 정점(최대 사거리) 각도로 고정
    peak_i = max(range(len(pts)), key=lambda i: dists[i])
    return clamp(angs[peak_i]), "too_far"


if __name__ == "__main__":
    # 빠른 점검/캘리브레이션: config 값으로 각도별 도달거리 + 역산 예시 출력
    from app.config import config

    b = config.ballistic
    print(f"v0(무게 {b.projectile_weight}kg) = "
          f"{muzzle_velocity(b.projectile_weight, b.spring_k, b.spring_x):.2f} m/s")
    print("angle(deg) -> distance(m)")
    for ang in range(0, int(config.aiming.tilt_max_deg) + 1, 5):
        d = forward_distance(ang, b.projectile_weight, b.projectile_air_resistance,
                             b.spring_k, b.spring_x, b.launch_height_m, b.gravity)
        print(f"  {ang:3d}°  ->  {d:.3f} m")
    print("distance(m) -> angle(deg)")
    for tgt in (0.3, 0.5, 0.8, 1.0, 1.5, 2.0):
        ang, st = solve_angle(tgt, b.projectile_weight, b.projectile_air_resistance,
                              b.spring_k, b.spring_x, b.launch_height_m,
                              float(config.aiming.tilt_min_deg),
                              float(config.aiming.tilt_max_deg), b.gravity)
        print(f"  {tgt:.2f} m  ->  {ang:.2f}°  ({st})")
