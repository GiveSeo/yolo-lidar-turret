"""조준 컨트롤러 + MLP 각도 모델 단위 테스트.

실행: .venv\\Scripts\\python -m tests.test_aiming
"""
import tempfile
from pathlib import Path

from app.aiming.controller import compute_error, next_aim_angles


def test_centered_within_tolerance():
    # 화면 640x480, 중앙(320,240) 근처면 centered=True
    err = compute_error(322, 241, 640, 480)
    assert err.centered is True


def test_error_sign_and_offcenter():
    err = compute_error(420, 300, 640, 480)  # 오른쪽/아래
    assert err.dx == 100 and err.dy == 60
    assert err.centered is False


def test_next_angles_move_toward_center():
    # dx>0(오른쪽), dy>0(아래). invert 플래그에 따라 pan/tilt 부호가 바뀐다.
    from app.config import config
    err = compute_error(440, 300, 640, 480)  # dx=120, dy=60
    a = config.aiming
    orig_pan, orig_tilt = a.invert_pan, a.invert_tilt
    try:
        # 비반전: dx>0 -> pan 증가, dy>0+invert_tilt -> tilt 감소
        a.invert_pan, a.invert_tilt = False, True
        pan, tilt = next_aim_angles(err, pan_current=90, tilt_current=90)
        assert pan > 90 and tilt < 90
        # pan 반전: dx>0 -> pan 감소 (좌우 트래킹 반대 하드웨어)
        a.invert_pan = True
        pan2, _ = next_aim_angles(err, pan_current=90, tilt_current=90)
        assert pan2 < 90
    finally:
        a.invert_pan, a.invert_tilt = orig_pan, orig_tilt


def test_angle_limits_and_step_clamp():
    # 큰 오차여도 max_step_deg(5) 이상 한번에 움직이지 않음 (tilt 범위 0~45 안에서 시작)
    err = compute_error(640, 480, 640, 480)  # dx=320, dy=240 (큰 오차)
    pan, tilt = next_aim_angles(err, pan_current=90, tilt_current=20)
    assert abs(pan - 90) <= 5 and abs(tilt - 20) <= 5
    # 경계 클램프 (tilt 도 0~45 로 클램프되는지)
    pan2, tilt2 = next_aim_angles(err, pan_current=180, tilt_current=44)
    assert pan2 <= 180 and 0 <= tilt2 <= 45


def test_angle_mlp_train_and_predict():
    """작은 합성 데이터로 MLP 학습 후 추론 출력 shape/키를 확인."""
    import torch
    from app.aiming.angle_model import AngleMLP, AnglePredictor

    in_features = ["center_x", "center_y", "distance"]
    out_features = ["pan", "tilt"]
    model = AngleMLP(in_dim=3, out_dim=2, hidden=(16, 16))

    ckpt = {
        "model_state": model.state_dict(),
        "in_features": in_features,
        "out_features": out_features,
        "x_mean": [320.0, 240.0, 1500.0],
        "x_std": [100.0, 80.0, 500.0],
        "y_mean": [90.0, 90.0],
        "y_std": [20.0, 20.0],
        "hidden": [16, 16],
    }
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "m.pt"
        torch.save(ckpt, path)
        pred = AnglePredictor(path, device="cpu")
        out = pred.predict([330.0, 250.0, 1600.0])
        assert set(out.keys()) == {"pan", "tilt"}
        assert all(isinstance(v, float) for v in out.values())


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("aiming: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
