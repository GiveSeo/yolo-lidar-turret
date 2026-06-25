"""피에조 충격 세기 → 점수/등급 환산.

ESP32 가 보낸 피크값(sensor_value)을 0~max_score 점수로 매핑하고 등급을 부여한다.
값이 없으면(센서값 미전송) None 을 반환한다 — 이 경우 명중 여부(HIT/MISS)만 남는다.
"""
from __future__ import annotations

from typing import Optional

from app.config import config

# 점수 구간별 등급 (점수 >= 경계면 해당 등급). 위에서부터 평가.
_GRADES = [(90, "S"), (75, "A"), (60, "B"), (40, "C"), (1, "D"), (0, "F")]


def grade_for(score: float) -> str:
    for threshold, label in _GRADES:
        if score >= threshold:
            return label
    return "F"


def score_for(sensor_value: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """피크값 -> (점수, 등급). 값이 없으면 (None, None)."""
    if sensor_value is None:
        return None, None
    sc = config.score
    span = sc.sensor_max - sc.sensor_min
    if span <= 0:
        frac = 1.0 if sensor_value >= sc.sensor_max else 0.0
    else:
        v = max(sc.sensor_min, min(sc.sensor_max, float(sensor_value)))
        frac = (v - sc.sensor_min) / span
    score = round(frac * sc.max_score, 1)
    return score, grade_for(score)
