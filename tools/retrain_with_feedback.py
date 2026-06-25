"""실측 발사 피드백을 반영해 발사각 MLP 를 재학습한다.

실측 로그(data/feedback/shots.csv)를 시뮬 데이터(data/angle/projectile.csv)와 혼합해
(또는 실측 단독으로) train.train_angle_mlp 를 재실행한다. 재학습 후 모델이 잔차를
흡수했으므로 tilt_bias 를 0 으로 리셋할 수 있다.

실행:
  # 시뮬 + 실측 혼합 재학습 후 bias 리셋
  .venv\\Scripts\\python -m tools.retrain_with_feedback --reset-bias
  # 실측만으로 재학습
  .venv\\Scripts\\python -m tools.retrain_with_feedback --real-only

두 CSV 모두 (weight, air_resistance, angle, landing_distance) 컬럼을 가진다.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

from app.config import ANGLE_CSV_DIR, SHOTS_CSV

SIM_CSV = ANGLE_CSV_DIR / "projectile.csv"
COMBINED_CSV = ANGLE_CSV_DIR / "_combined_sim_real.csv"
COLS = ["weight", "air_resistance", "angle", "landing_distance"]


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_combined(real_only: bool) -> tuple[Path, int, int]:
    sim = [] if real_only else _read_rows(SIM_CSV)
    real = _read_rows(SHOTS_CSV)
    if not real:
        raise SystemExit(f"실측 로그가 없습니다: {SHOTS_CSV}")

    COMBINED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(COMBINED_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for src in (sim, real):
            for r in src:
                w.writerow({c: r[c] for c in COLS})
    return COMBINED_CSV, len(sim), len(real)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-only", action="store_true", help="실측 로그만으로 학습")
    ap.add_argument("--reset-bias", action="store_true", help="재학습 후 tilt_bias 0 리셋")
    ap.add_argument("--epochs", type=int, default=800)
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()

    combined, n_sim, n_real = build_combined(args.real_only)
    print(f"혼합 데이터: sim {n_sim} + real {n_real} -> {combined}")

    cmd = [
        sys.executable, "-m", "train.train_angle_mlp",
        "--csv", str(combined),
        "--inputs", "weight,air_resistance,landing_distance",
        "--outputs", "angle",
        "--epochs", str(args.epochs), "--batch", str(args.batch),
    ]
    print("재학습 실행:", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        raise SystemExit(f"재학습 실패(코드 {rc})")

    if args.reset_bias:
        from app.feedback import FeedbackStore
        FeedbackStore().reset_bias()
        print("tilt_bias 0 으로 리셋(모델이 잔차 흡수).")
    print("완료.")


if __name__ == "__main__":
    main()
