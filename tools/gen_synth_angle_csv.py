"""파이프라인 검증용 합성 각도 CSV 생성기.

형님의 실제 PyBullet CSV 가 준비되기 전, train_angle_mlp.py 와 추론 경로를
검증하기 위한 데모 데이터를 만든다. (center_x, center_y, distance) -> (pan, tilt)
의 임의의 학습 가능한 관계를 따른다.

실행: .venv\\Scripts\\python -m tools.gen_synth_angle_csv
산출물: data/angle/_demo_synthetic.csv  (실제 데이터로 교체 대상)
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from app.config import ANGLE_CSV_DIR


def main(n: int = 2000) -> None:
    ANGLE_CSV_DIR.mkdir(parents=True, exist_ok=True)
    out = ANGLE_CSV_DIR / "_demo_synthetic.csv"
    # 결정적 의사난수 (외부 의존 없이 재현 가능)
    seed = 12345
    def rnd():
        nonlocal seed
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        return seed / 0x7FFFFFFF

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["center_x", "center_y", "distance", "pan", "tilt"])
        for _ in range(n):
            cx = rnd() * 640
            cy = rnd() * 480
            dist = 800 + rnd() * 2500  # mm
            # 임의의 학습 가능한 관계 (포물선 낙하 보정 모사)
            pan = 90 + (cx - 320) * 0.06
            drop = 0.0000035 * dist * dist  # 거리에 따른 탄도 보정(상향)
            tilt = 90 - (cy - 240) * 0.06 + drop
            w.writerow([f"{cx:.2f}", f"{cy:.2f}", f"{dist:.1f}",
                        f"{pan:.3f}", f"{tilt:.3f}"])
    print(f"합성 CSV 생성: {out} ({n} 행)")


if __name__ == "__main__":
    main()
