"""탄도 CSV 표준화.

원본(예: 0602_5Ns.csv)의 컬럼:
    Weight(kg), Air_Resistance(kg/s), Angle(deg), Landing_Distance(m)
을 단위 괄호 없는 표준 컬럼명으로 변환해 data/angle/projectile.csv 로 저장한다.
    weight, air_resistance, angle, landing_distance

발사각 모델은 역방향으로 학습한다:
    입력 = (weight, air_resistance, landing_distance) -> 출력 = angle

실행: .venv\\Scripts\\python -m tools.prepare_ballistic_csv <원본csv> [출력csv]
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from app.config import ANGLE_CSV_DIR

COLMAP = {
    "weight(kg)": "weight",
    "air_resistance(kg/s)": "air_resistance",
    "angle(deg)": "angle",
    "landing_distance(m)": "landing_distance",
}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m tools.prepare_ballistic_csv <원본csv> [출력csv]")
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ANGLE_CSV_DIR / "projectile.csv"
    dst.parent.mkdir(parents=True, exist_ok=True)

    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        src_cols = reader.fieldnames or []
        # 원본 컬럼 -> 표준 컬럼 매핑(소문자/공백제거 비교)
        norm = {c.lower().replace(" ", ""): c for c in src_cols}
        out_cols = ["weight", "air_resistance", "angle", "landing_distance"]
        resolved = {}
        for std_key, std_name in COLMAP.items():
            if std_key in norm:
                resolved[std_name] = norm[std_key]
        missing = [c for c in out_cols if c not in resolved]
        if missing:
            raise SystemExit(f"원본에서 컬럼을 찾지 못함: {missing}\n원본 컬럼: {src_cols}")

        rows = list(reader)

    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for r in rows:
            w.writerow([r[resolved[c]] for c in out_cols])

    print(f"표준화 완료: {dst} ({len(rows)} 행)")
    print(f"컬럼: {out_cols}")


if __name__ == "__main__":
    main()
