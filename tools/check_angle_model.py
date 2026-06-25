"""학습된 발사각 모델의 추론 sanity check.

설정된 발사체 스펙(config.ballistic)으로 거리별 발사각을 출력한다.
실행: .venv\\Scripts\\python -m tools.check_angle_model
"""
from app.aiming.angle_model import load_predictor
from app.config import config

p = load_predictor(device="cpu")
if p is None:
    raise SystemExit("angle_mlp.pt 가 없습니다. 먼저 학습하세요.")

w = config.ballistic.projectile_weight
a = config.ballistic.projectile_air_resistance
print(f"in_features={p.in_features} -> out={p.out_features}")
print(f"projectile: weight={w}kg, air_resistance={a}")
for d in [0.21, 0.24, 0.25, 0.27, 0.28, 0.295]:
    feat = {"weight": w, "air_resistance": a, "landing_distance": d, "distance": d}
    features = [feat[name] for name in p.in_features]
    ang = p.predict(features)["angle"]
    print(f"  dist={d:.3f} m -> launch_angle={ang:5.1f} deg")
