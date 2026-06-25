"""피드백 기반 발사각 보정 (sim-to-real).

운영자가 발사 후 '앞/뒤 + cm' 오차를 입력하면, 모델 기준 잔차로 tilt_bias 를 갱신하고
실측 발사 기록을 CSV 로 누적한다. bias 는 calibration.json 에 영속된다.

잔차 보정 수식:
    발사 시: used_angle = model(weight, air, target) + tilt_bias 로 쐈고,
    실제로 actual = target + error_m 에 착탄했다면,
    residual = used_angle - model(weight, air, actual)
    tilt_bias <- clamp(tilt_bias + bias_lr * residual, ±bias_max)

model(actual) 은 '그 실제 거리를 내는 데 모델상 필요한 각도'다. used_angle 과의 차이가
모델 오차이므로 bias 로 흡수한다. 탄도 각도-거리 단조성과 무관하게 부호가 맞다.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Optional

from app.config import CALIBRATION_JSON, FEEDBACK_DIR, SHOTS_CSV, config
from app.state import state

logger = logging.getLogger(__name__)

SHOTS_HEADER = ["weight", "air_resistance", "angle", "landing_distance",
                "target_distance", "error_cm", "result", "ts"]


class FeedbackStore:
    def __init__(self, predictor=None) -> None:
        self.predictor = predictor

    # --- 영속 ----------------------------------------------------------
    def load(self) -> None:
        """calibration.json 의 tilt_bias/pan_bias 와 shots.csv 행 수를 state 에 로드."""
        bias = 0.0
        pan_bias = 0.0
        if CALIBRATION_JSON.exists():
            try:
                data = json.loads(CALIBRATION_JSON.read_text())
                bias = float(data.get("tilt_bias", 0.0))
                pan_bias = float(data.get("pan_bias", 0.0))
            except (ValueError, OSError, json.JSONDecodeError) as e:
                logger.warning("calibration.json 로드 실패: %s", e)
        count = 0
        if SHOTS_CSV.exists():
            with open(SHOTS_CSV, newline="", encoding="utf-8") as f:
                count = max(0, sum(1 for _ in f) - 1)  # 헤더 제외
        with state.lock:
            state.tilt_bias = bias
            state.pan_bias = pan_bias
            state.shots_count = count
        logger.info("피드백 로드: tilt_bias=%.2f, pan_bias=%.2f, shots=%d", bias, pan_bias, count)

    def _save(self, tilt_bias: float, pan_bias: float) -> None:
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        CALIBRATION_JSON.write_text(json.dumps(
            {"tilt_bias": round(tilt_bias, 4), "pan_bias": round(pan_bias, 4)}))

    def _append_shot(self, row: dict) -> None:
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        new = not SHOTS_CSV.exists()
        with open(SHOTS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SHOTS_HEADER)
            if new:
                w.writeheader()
            w.writerow(row)

    # --- 핵심 ----------------------------------------------------------
    def _residual(self, weight: float, air: float, used_angle: float,
                  actual_m: float, error_m: float) -> float:
        """잔차(도) = 실제 착탄거리를 내는 '기준 각도' - 우리가 쏜 각도.

        우선순위: 실측표 > MLP > 폴백계수. 표가 있으면 표가 발사각의 진실이므로
        표에서 actual_m 에 해당하는 각도를 기준으로 삼는다(표와 일관된 보정).
        """
        if config.ballistic.calib_table:
            from app.aiming.ballistic_solver import solve_angle_table
            ang, _ = solve_angle_table(
                actual_m, config.ballistic.calib_table,
                float(config.aiming.tilt_min_deg), float(config.aiming.tilt_max_deg))
            return used_angle - ang
        if self.predictor is not None:
            feat = {"weight": weight, "air_resistance": air,
                    "landing_distance": actual_m, "distance": actual_m}
            try:
                features = [feat[n] for n in self.predictor.in_features]
                model_actual = self.predictor.predict(features)["angle"]
                return used_angle - model_actual
            except Exception as e:  # noqa: BLE001
                logger.warning("잔차 계산 폴백: %s", e)
        # 폴백: 무저항 가정(각도↑->거리↑). 앞(error<0)이면 거리 늘리도록 각도↑.
        return -(error_m * 100.0) * config.ballistic.fallback_deg_per_cm

    def _pan_residual(self, side_m: float, distance_m: float) -> float:
        """좌우 착탄오차(side_m: +오른쪽, -왼쪽)를 pan 보정 각도(도)로 환산.

        거리를 알면 각도 = atan(가로오차/거리), 모르면 cm->deg 폴백 계수.
        오른쪽(+)으로 빗나갔으면 pan_feedback_sign 방향으로 pan_bias 를 민다.
        """
        import math
        bal = config.ballistic
        if side_m == 0:
            return 0.0
        if distance_m and distance_m > 0:
            deg = math.degrees(math.atan(abs(side_m) / distance_m))
        else:
            deg = abs(side_m) * 100.0 * bal.pan_fallback_deg_per_cm
        sign = 1.0 if side_m > 0 else -1.0
        return bal.pan_feedback_sign * sign * deg

    def apply_feedback(self, result: str, error_m: float, ts: float,
                       side_m: float = 0.0) -> dict:
        """직전 발사(state.last_shot)에 대한 운영자 피드백을 반영(tilt + pan).

        error_m: 앞뒤 착탄오차(앞 -, 뒤 +, 0=명중/미상) -> tilt_bias 갱신
        side_m : 좌우 착탄오차(왼 -, 오른 +, 0=정확/미상) -> pan_bias 갱신
        반환: {ok, tilt_bias, residual, pan_bias, pan_residual, shots_count}
        """
        with state.lock:
            shot = state.last_shot
            prev_bias = state.tilt_bias
            prev_pan = state.pan_bias
        if not shot:
            return {"ok": False, "reason": "직전 발사 기록 없음"}

        target_m = shot["target_distance_m"]
        weight = shot["weight"]
        air = shot["air"]
        used_angle = shot["used_launch_angle"]
        actual_m = target_m + error_m

        # tilt(앞뒤) 보정
        residual = self._residual(weight, air, used_angle, actual_m, error_m)
        bias_max = config.ballistic.bias_max_deg
        new_bias = max(-bias_max, min(bias_max, prev_bias + config.ballistic.bias_lr * residual))

        # pan(좌우) 보정
        pan_res = self._pan_residual(side_m, target_m)
        pan_max = config.ballistic.pan_bias_max_deg
        new_pan = max(-pan_max, min(pan_max, prev_pan + config.ballistic.pan_bias_lr * pan_res))

        self._save(new_bias, new_pan)
        self._append_shot({
            "weight": weight,
            "air_resistance": air,
            "angle": round(used_angle, 4),
            "landing_distance": round(actual_m, 4),
            "target_distance": round(target_m, 4),
            "error_cm": round(error_m * 100.0, 2),
            "result": result,
            "ts": round(ts, 3),
        })

        with state.lock:
            state.tilt_bias = new_bias
            state.pan_bias = new_pan
            state.shots_count += 1
            count = state.shots_count

        logger.info("피드백 반영: result=%s 앞뒤=%.1fcm(res %.2f°->tilt %.2f°) "
                    "좌우=%.1fcm(res %.2f°->pan %.2f°)",
                    result, error_m * 100, residual, new_bias,
                    side_m * 100, pan_res, new_pan)
        return {"ok": True,
                "tilt_bias": round(new_bias, 2), "residual": round(residual, 2),
                "pan_bias": round(new_pan, 2), "pan_residual": round(pan_res, 2),
                "shots_count": count}

    def reset_bias(self) -> None:
        """재학습으로 모델이 잔차를 흡수한 뒤 tilt/pan bias 를 0 으로 리셋."""
        self._save(0.0, 0.0)
        with state.lock:
            state.tilt_bias = 0.0
            state.pan_bias = 0.0
        logger.info("tilt_bias/pan_bias 리셋(0)")
