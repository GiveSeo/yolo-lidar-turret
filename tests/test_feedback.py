"""피드백 보정(app/feedback.py) 단위 테스트.

잔차 부호/누적/clamp 와 calibration.json 영속을 검증한다. 모듈 전역 경로를 임시
디렉터리로 패치하고, 단조 감소(drag 유사) FakePredictor 를 사용한다.

실행: .venv\\Scripts\\python -m tests.test_feedback
"""
import tempfile
from pathlib import Path

import app.feedback as fb
from app.config import config
from app.state import state


class FakePredictor:
    """angle = 90 - distance_m*10 (단조 감소: 거리↑ -> 각도↓, drag 유사)."""
    in_features = ["weight", "air_resistance", "landing_distance"]
    out_features = ["angle"]

    def predict(self, features):
        distance = features[2]
        return {"angle": 90.0 - distance * 10.0}


def _set_paths(tmp: Path):
    fb.FEEDBACK_DIR = tmp
    fb.SHOTS_CSV = tmp / "shots.csv"
    fb.CALIBRATION_JSON = tmp / "calibration.json"


def _set_shot(target_m, used_angle, weight=0.003, air=0.05):
    with state.lock:
        state.tilt_bias = 0.0
        state.shots_count = 0
        state.last_shot = {
            "target_distance_m": target_m, "weight": weight, "air": air,
            "model_angle": used_angle, "used_launch_angle": used_angle,
            "pan": 90, "tilt_servo": 120,
        }


def test_short_makes_bias_negative():
    # target 0.25 에서 used=87.5 로 쐈는데 0.20(앞,짧음)에 착탄
    with tempfile.TemporaryDirectory() as d:
        _set_paths(Path(d))
        _set_shot(0.25, used_angle=87.5)
        store = fb.FeedbackStore(predictor=FakePredictor())
        r = store.apply_feedback("miss", error_m=-0.05, ts=1.0)
        # model(0.20)=88 -> residual=87.5-88=-0.5 -> bias=lr*(-0.5)
        assert r["ok"]
        assert r["residual"] < 0
        assert r["tilt_bias"] < 0
        assert abs(r["tilt_bias"] - config.ballistic.bias_lr * -0.5) < 1e-6


def test_long_makes_bias_positive():
    with tempfile.TemporaryDirectory() as d:
        _set_paths(Path(d))
        _set_shot(0.25, used_angle=87.5)
        store = fb.FeedbackStore(predictor=FakePredictor())
        r = store.apply_feedback("miss", error_m=0.05, ts=1.0)  # 0.30 (뒤,김)
        assert r["residual"] > 0 and r["tilt_bias"] > 0


def test_bias_clamped():
    with tempfile.TemporaryDirectory() as d:
        _set_paths(Path(d))
        _set_shot(0.25, used_angle=87.5)
        store = fb.FeedbackStore(predictor=FakePredictor())
        # 거대한 오차 -> clamp 범위 내로 제한
        r = store.apply_feedback("miss", error_m=100.0, ts=1.0)
        assert abs(r["tilt_bias"]) <= config.ballistic.bias_max_deg


def test_calibration_persist_and_load():
    with tempfile.TemporaryDirectory() as d:
        _set_paths(Path(d))
        _set_shot(0.25, used_angle=87.5)
        store = fb.FeedbackStore(predictor=FakePredictor())
        r = store.apply_feedback("miss", error_m=-0.05, ts=1.0)
        saved = r["tilt_bias"]
        # state 를 흐트러뜨린 뒤 load 로 복원되는지
        with state.lock:
            state.tilt_bias = 0.0
            state.shots_count = 0
        store.load()
        assert abs(state.tilt_bias - saved) < 1e-3
        assert state.shots_count == 1   # shots.csv 1행


def test_no_last_shot():
    with tempfile.TemporaryDirectory() as d:
        _set_paths(Path(d))
        with state.lock:
            state.last_shot = None
        store = fb.FeedbackStore(predictor=FakePredictor())
        r = store.apply_feedback("miss", error_m=-0.05, ts=1.0)
        assert r["ok"] is False


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("feedback: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
