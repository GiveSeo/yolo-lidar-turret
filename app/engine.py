"""조준/발사 오케스트레이션 엔진.

표적이 선택된 뒤 사용자가 발사 시퀀스를 시작하면, 다음 상태머신을 별도
스레드에서 수행한다:

    AIMING  : 객체를 화면 중앙으로 보내는 P 제어 루프 (CONTROL 반복 전송)
    RANGING : LiDAR 최신 거리 확보
    FIRING  : MLP 로 발사 각도 추론 -> 이동 -> trigger=1 발사
    JUDGING : 발사 후 hit_wait_seconds 동안 ESP32 명중 신호 대기
    RESULT  : HIT / MISS 확정

RPi 실물이 없어도 목 클라이언트(STATUS/LIDAR 응답)만 있으면 전체 흐름이 돈다.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

from app.aiming.controller import compute_error, next_aim_angles
from app.comms.esp32_hit import HitMonitor
from app.comms.rpi_link import RpiLink
from app.config import config
from app.state import Phase, state

logger = logging.getLogger(__name__)


class AimingEngine:
    def __init__(self, link: RpiLink, hit_monitor: HitMonitor, predictor=None) -> None:
        self.link = link
        self.hit_monitor = hit_monitor
        self.predictor = predictor  # AnglePredictor | None
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Lock()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def engage(self, aim_only: bool = False) -> bool:
        """발사 시퀀스를 시작한다. 이미 진행 중이거나 표적 미선택이면 False.

        aim_only=True 면 조준(트래킹)만 하고 측거/발사는 건너뛴다(발사 안 함, 안전 테스트용).
        """
        if self.is_running():
            return False
        if state.selected_detection() is None:
            return False
        self._thread = threading.Thread(target=self._run, args=(aim_only,),
                                        name="aim-engine", daemon=True)
        self._thread.start()
        return True

    def _set(self, phase: Phase, message: str = "", result: Optional[str] = None) -> None:
        with state.lock:
            state.phase = phase
            if message:
                state.message = message
            if result is not None:
                state.result = result
        logger.info("[engine] %s %s", phase.value, message)

    def _run(self, aim_only: bool = False) -> None:
        with self._busy:
            try:
                if not self._aim():
                    self._set(Phase.IDLE, "조준 실패(표적 소실)")
                    return
                if aim_only:
                    self._set(Phase.IDLE, "조준만 완료(트래킹 테스트) — 발사 안 함")
                    return
                # 조준 자세에서 한 번 정착시킨 뒤 발사 시퀀스로(따로 누르던 효과 = 명중률↑)
                delay = max(0.0, config.fire.post_aim_delay_s)
                if delay:
                    self._set(Phase.AIMING, f"조준 완료 — 발사 준비 대기({delay:.0f}s)")
                    time.sleep(delay)
                self._range()
                self._fire()
                self._judge()
                self._recenter()
            except Exception as e:  # 엔진이 죽어도 서버는 살아있게
                logger.exception("엔진 오류: %s", e)
                self._set(Phase.IDLE, f"엔진 오류: {e}")

    # --- 단계 구현 -------------------------------------------------------
    def _aim(self, max_iters: int = 500, settle_frames: int = 2) -> bool:
        """객체가 화면 중앙에 올 때까지 P 제어로 조준한다."""
        self._set(Phase.AIMING, "자동 조준 중")
        centered_streak = 0
        lost = 0
        stuck = 0              # pan 이 서보 한계에 박혀 더 못 가는 상태 카운트
        was_centered = False   # 직전에 중앙 정렬돼 있었는지(LiDAR IR 글레어로 중앙서 미검출 대비)
        for _ in range(max_iters):
            det = state.selected_detection()
            if det is None:
                # 표적이 거의 중앙에 와서 검출이 끊긴 경우(LiDAR 빛 글레어)는 정렬 완료로 인정
                if was_centered:
                    centered_streak += 1
                    if centered_streak >= settle_frames:
                        self._set(Phase.AIMING, "중앙 정렬 완료(글레어 허용)")
                        return True
                    time.sleep(0.05)
                    continue
                # 그 외 미검출은 견딘다(재바인딩 + 자동 재선택 시간 확보)
                lost += 1
                if lost > 120:  # ~6초 연속 소실이면 조준 실패(자동 재선택 창보다 길게)
                    return False
                time.sleep(0.05)
                continue
            lost = 0
            with state.lock:
                fw, fh = state.frame_width, state.frame_height
                pan_cur, tilt_cur = state.pan_current, state.tilt_current
            if fw == 0 or fh == 0:
                time.sleep(0.05)
                continue

            cx, cy = det.center
            err = compute_error(cx, cy, fw, fh, box_w=det.x2 - det.x1)
            if err.centered:
                was_centered = True
                centered_streak += 1
                if centered_streak >= settle_frames:
                    self._set(Phase.AIMING, "중앙 정렬 완료")
                    return True
            else:
                was_centered = False
                centered_streak = 0
                pan_t, tilt_t = next_aim_angles(err, pan_cur, tilt_cur)
                self.link.send_control(pan_t, tilt_t, trigger=0)
                # pan 이 한계(0/180)에 박혔는데도 더 가라고 요구하면(pan_t==pan_cur) 영원히
                # 못 맞춘다 -> 잠깐 견디다 포기(40초 멈춤 방지 + 방향/범위 문제 알림).
                a = config.aiming
                at_rail = pan_cur <= a.pan_min_deg or pan_cur >= a.pan_max_deg
                if at_rail and pan_t == pan_cur:
                    stuck += 1
                    if stuck > 25:  # ~2초
                        self._set(Phase.IDLE,
                                  "조준 한계: pan 서보 범위 초과(표적이 가동범위 밖이거나 방향 반대?)")
                        return False
                else:
                    stuck = 0
            time.sleep(0.08)  # 서보 이동 + 다음 프레임 대기
        return False

    def _range(self, timeout: float = 3.0) -> None:
        """측거 요청을 보내 LiDAR 1회 측정값을 확보한다(트리거 모드).

        조준 완료 후 이 시점에만 LiDAR 가 켜져 측정한다(조준 중에는 IR off → 글레어 없음).
        """
        self._set(Phase.RANGING, "거리 측정 중")
        with state.lock:
            state.distance_mm = None      # 새 측정 대기 위해 이전 값 무효화
        deadline = time.monotonic() + timeout
        next_req = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_req:
                self.link.send_range_request()   # 0.5s 마다 재요청(트리거 누락 대비)
                next_req = time.monotonic() + 0.5
            with state.lock:
                dist = state.distance_mm
            if dist is not None and dist > 0:
                self._set(Phase.RANGING, f"거리 {dist} mm")
                return
            time.sleep(0.05)
        self._set(Phase.RANGING, "거리 측정 실패(폴백)")

    def _fire(self) -> None:
        """발사 각도 추론 -> 이동 -> 발사."""
        self._set(Phase.FIRING, "발사 각도 계산")
        det = state.selected_detection()
        with state.lock:
            fw, fh = state.frame_width, state.frame_height
            dist = state.distance_mm or 0
            pan_cur, tilt_cur = state.pan_current, state.tilt_current

        pan_fire, tilt_fire = self._predict_angles(det, fw, fh, dist, pan_cur, tilt_cur)
        # 발사 각도로 이동 (trigger=0)
        self.link.send_control(pan_fire, tilt_fire, trigger=0)
        settle = max(0.0, config.fire.fire_settle_s)
        self._set(Phase.FIRING, f"조준 정착 대기({settle:.1f}s)")
        time.sleep(settle)  # 서보 이동 후 흔들림 정착 대기

        # 발사 직전 명중 모니터 무장 후 trigger=1
        self.hit_monitor.arm()
        with state.lock:
            state.fire_time = time.time()
            state.result = None
        self.link.send_control(pan_fire, tilt_fire, trigger=config.fire.trigger_release_value)
        self._set(Phase.FIRING, f"발사! pan={pan_fire} tilt={tilt_fire}")

    def _predict_angles(self, det, fw, fh, dist, pan_cur, tilt_cur) -> tuple[int, int]:
        """발사 각도 산출.

        형님 방식: pan(좌우)은 조준으로 맞춘 값을 그대로 유지하고, tilt(상하)만
        거리 기반 탄도 발사각으로 바꿔 발사한다.

        MLP 입력 = (weight, air_resistance, landing_distance[m]) -> 출력 = angle[deg]
        tilt_servo = tilt_horizontal_deg + up_sign * angle
        MLP 가 없으면 현재 조준 tilt 를 그대로 사용(폴백).
        """
        bal = config.ballistic
        distance_m = float(dist) * bal.mm_to_m
        # 좌우(pan)는 조준값 + 고정 조준 오프셋(거리 무관, 오른쪽 보정용).
        pan_fire = max(config.aiming.pan_min_deg,
                       min(config.aiming.pan_max_deg,
                           int(round(pan_cur + bal.pan_aim_offset_deg))))

        # 발사각(model_angle) 산출 우선순위: 실측표 > 해석식 > MLP
        model_angle = None
        angle_status = "ok"
        method = "MLP"
        from_table = False
        if bal.calib_table:
            from app.aiming.ballistic_solver import solve_angle_table
            method = "실측표"
            from_table = True
            model_angle, angle_status = solve_angle_table(
                distance_m, bal.calib_table,
                float(config.aiming.tilt_min_deg), float(config.aiming.tilt_max_deg))
        elif bal.use_analytical_angle:
            from app.aiming.ballistic_solver import solve_angle
            method = "해석식"
            model_angle, angle_status = solve_angle(
                distance_m, bal.projectile_weight, bal.projectile_air_resistance,
                bal.spring_k, bal.spring_x, bal.launch_height_m,
                angle_min=float(config.aiming.tilt_min_deg),
                angle_max=float(config.aiming.tilt_max_deg), g=bal.gravity,
            )
        elif self.predictor is not None:
            # 학습 CSV 컬럼명에 맞춘 피처 매핑 (in_features 순서대로 전달)
            feat_map = {
                "weight": bal.projectile_weight,
                "air_resistance": bal.projectile_air_resistance,
                "landing_distance": distance_m,
                "distance": distance_m,
            }
            try:
                features = [feat_map[name] for name in self.predictor.in_features]
                out = self.predictor.predict(features)
                # 출력 컬럼명이 'angle' 이라고 가정(없으면 첫 출력 사용)
                model_angle = out.get("angle", out.get(self.predictor.out_features[0]))
            except KeyError as e:
                logger.warning("MLP 입력 피처 %s 를 매핑할 수 없어 폴백합니다.", e)

        if model_angle is None:
            return pan_fire, tilt_cur   # 해석식/MLP 둘 다 없으면 현재 tilt 유지(폴백)

        # 피드백 누적 보정(tilt_bias) 적용
        with state.lock:
            bias = state.tilt_bias
        if from_table:
            # 실측표는 '명령 tilt -> 실측 거리'라 표의 각도가 곧 서보각이다.
            # 시차/수평오프셋은 이미 측정에 반영돼 있으므로 더하지 않는다.
            parallax_deg = 0.0
            used_launch_angle = model_angle + bias
            tilt_servo = used_launch_angle
        else:
            # 발사구가 카메라보다 위에 있는 시차 보정: 표적이 발사구 기준 더 아래로 보이므로
            # 거리가 가까울수록 더 많이 tilt 를 내린다(atan(offset/distance)).
            parallax_deg = 0.0
            if distance_m > 0 and bal.launcher_above_camera_m:
                parallax_deg = math.degrees(math.atan(bal.launcher_above_camera_m / distance_m))
            used_launch_angle = model_angle + bias - parallax_deg
            tilt_servo = bal.tilt_horizontal_deg + bal.tilt_up_sign * used_launch_angle
        tilt_servo = max(config.aiming.tilt_min_deg,
                         min(config.aiming.tilt_max_deg, int(round(tilt_servo))))
        if angle_status != "ok":
            logger.warning("[engine] 해석식 발사각: 거리 %.2fm 도달범위 밖(%s) -> 경계각 %d° 사용",
                           distance_m, angle_status, int(round(model_angle)))

        # 피드백을 위해 직전 발사 정보 기록
        with state.lock:
            state.last_shot = {
                "target_distance_m": distance_m,
                "weight": bal.projectile_weight,
                "air": bal.projectile_air_resistance,
                "model_angle": float(model_angle),
                "used_launch_angle": float(used_launch_angle),
                "pan": int(pan_fire),
                "tilt_servo": int(tilt_servo),
            }
        logger.info("[engine] 발사각(%s): dist=%.3fm angle=%.2f° bias=%.2f° 시차=%.2f° -> used=%.2f° tilt_servo=%d",
                    method, distance_m, model_angle, bias, parallax_deg, used_launch_angle, tilt_servo)
        return pan_fire, tilt_servo

    def _recenter(self) -> None:
        """발사 후 포탑(pan)을 차체 정면(중립)으로 복귀시켜 차체 방향과 카메라 방향을 맞춘다.

        차체(자동차 조향)는 제자리 회전이 안 되므로 차체를 돌리는 대신, 포탑 pan 을
        정면(pan_home_deg)으로 되돌려 '차체 방향 = 카메라 방향'을 일치시킨다.
        tilt(상하)는 방위와 무관하므로 현재값을 유지한다.
        """
        if not config.fire.recenter_after_fire:
            return
        # 발사/판정 직후 곧바로 돌리지 않고 잠깐 여운을 둔 뒤 정렬한다.
        time.sleep(max(0.0, config.fire.recenter_delay_s))
        with state.lock:
            tilt_cur = state.tilt_current
        pan_home = config.fire.pan_home_deg
        self.link.send_control(pan_home, tilt_cur, trigger=0)
        logger.info("[engine] 발사 후 포탑 정면 복귀: pan=%d tilt=%d", pan_home, tilt_cur)

    def _judge(self) -> None:
        """발사 후 정해진 시간 동안 명중 신호를 대기하여 HIT/MISS 판정 + 로그 적재.

        대기 시간을 1초 단위로 나눠 메시지를 갱신해 '명중 판정 대기(10s→9s→…→1s)'
        카운트다운으로 보이게 한다. 명중 신호가 오면 즉시 빠져나간다.
        """
        wait = config.fire.hit_wait_seconds
        self._set(Phase.JUDGING, f"명중 판정 대기({int(math.ceil(wait))}s)")
        deadline = time.monotonic() + wait
        hit = False
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            with state.lock:
                state.message = f"명중 판정 대기({int(math.ceil(rem))}s)"
            if self.hit_monitor.poll_hit(min(1.0, rem)):
                hit = True
                break
        self.hit_monitor.disarm()
        result = "HIT" if hit else "MISS"
        with state.lock:
            state.esp32_seen = state.esp32_seen or hit
        self._log_shot(result, hit)
        self._set(Phase.RESULT, f"결과: {result}", result=result)

    def _log_shot(self, result: str, hit: bool) -> None:
        """발사+판정 결과를 SQLite 로그에 한 행으로 적재(시각화/AI 분석 원천).

        명중 시 피에조 세기(sensor_value)를 점수/등급으로 환산해 함께 기록한다.
        """
        from app import db, scoring

        with state.lock:
            shot = dict(state.last_shot) if state.last_shot else {}
            distance_mm = state.distance_mm
            pan_cur, tilt_cur = state.pan_current, state.tilt_current
            tilt_bias = state.tilt_bias
            fire_time = state.fire_time

        info = self.hit_monitor.last_hit_info()
        latency_ms = None
        if hit and info.get("ts") and fire_time:
            latency_ms = round((info["ts"] - fire_time) * 1000.0, 1)

        dist_m = shot.get("target_distance_m")
        if dist_m is None and distance_mm is not None:
            dist_m = distance_mm * config.ballistic.mm_to_m

        # 피에조 세기 -> 점수/등급 (MISS 거나 센서값 미전송이면 None)
        sensor_value = info.get("sensor_value") if hit else None
        score, grade = scoring.score_for(sensor_value)

        row = {
            "result": result,
            "distance_m": round(dist_m, 4) if dist_m is not None else None,
            "distance_mm": distance_mm,
            "pan": shot.get("pan", pan_cur),
            "tilt_servo": shot.get("tilt_servo", tilt_cur),
            "used_angle": shot.get("used_launch_angle"),
            "model_angle": shot.get("model_angle"),
            "weight": shot.get("weight", config.ballistic.projectile_weight),
            "air": shot.get("air", config.ballistic.projectile_air_resistance),
            "tilt_bias": round(tilt_bias, 4),
            "hit_latency_ms": latency_ms,
            "sensor_value": info.get("sensor_value"),
            "esp32_ms": info.get("esp32_ms"),
            "score": score,
            "grade": grade,
        }
        rid = db.insert_shot(row)
        logger.info("[engine] 발사 로그 적재 id=%s result=%s dist=%s score=%s(%s) latency=%sms",
                    rid, result, row["distance_m"], score, grade, latency_ms)
