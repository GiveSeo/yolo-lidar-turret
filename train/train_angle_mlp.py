"""PyBullet CSV 데이터로 발사 각도 MLP 회귀 모델을 학습한다.

CSV 는 입력(객체 위치/거리 등)과 출력(pan/tilt 각도) 컬럼을 가진다.
실제 컬럼명을 모를 수 있으므로, 인자로 지정하거나 자동 추정한다.

실행 예:
    .venv\\Scripts\\python -m train.train_angle_mlp \\
        --csv data/angle/data.csv \\
        --inputs center_x,center_y,distance \\
        --outputs pan,tilt --epochs 300

인자를 생략하면 CSV 헤더를 출력하고 추정한 입출력 컬럼을 사용한다.
산출물: models/angle_mlp.pt (AnglePredictor 가 로드하는 구조)
"""
from __future__ import annotations

import argparse
import csv
import glob
from pathlib import Path

import torch
import torch.nn as nn

from app.aiming.angle_model import AngleMLP
from app.config import ANGLE_CSV_DIR, ANGLE_MODEL

# 입력/출력 컬럼 자동 추정용 후보 (소문자 비교)
INPUT_HINTS = ["center_x", "center_y", "cx", "cy", "norm_x", "norm_y",
               "distance", "distance_mm", "dist", "x", "y", "z"]
OUTPUT_HINTS = ["pan", "tilt", "pan_angle", "tilt_angle", "pan_target", "tilt_target"]


def load_rows(csv_paths: list[Path]) -> tuple[list[str], list[dict]]:
    header: list[str] = []
    rows: list[dict] = []
    for p in csv_paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                header = reader.fieldnames
            for r in reader:
                rows.append(r)
    return header, rows


def guess_columns(header: list[str]) -> tuple[list[str], list[str]]:
    low = {h.lower(): h for h in header}
    outs = [low[h] for h in OUTPUT_HINTS if h in low]
    # 출력은 pan/tilt 우선
    outs = [o for o in outs if o.lower() in ("pan", "tilt", "pan_angle", "tilt_angle")] or outs
    out_set = {o.lower() for o in outs}
    ins = [low[h] for h in INPUT_HINTS if h in low and h not in out_set]
    return ins, outs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="CSV 파일 또는 글롭. 생략 시 data/angle/*.csv")
    ap.add_argument("--inputs", default=None, help="입력 컬럼명 콤마 구분")
    ap.add_argument("--outputs", default=None, help="출력 컬럼명 콤마 구분 (기본 pan,tilt)")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", default="64,64")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--out", default=str(ANGLE_MODEL))
    args = ap.parse_args()

    if args.csv:
        csv_paths = [Path(p) for p in glob.glob(args.csv)]
    else:
        csv_paths = [Path(p) for p in glob.glob(str(ANGLE_CSV_DIR / "*.csv"))]
    if not csv_paths:
        raise SystemExit(f"CSV 를 찾을 수 없습니다: {args.csv or ANGLE_CSV_DIR}")

    header, rows = load_rows(csv_paths)
    print(f"CSV 파일 {len(csv_paths)}개, 행 {len(rows)}개")
    print(f"헤더: {header}")
    if not rows:
        raise SystemExit("데이터 행이 없습니다.")

    in_cols = args.inputs.split(",") if args.inputs else None
    out_cols = args.outputs.split(",") if args.outputs else None
    if in_cols is None or out_cols is None:
        gi, go = guess_columns(header)
        in_cols = in_cols or gi
        out_cols = out_cols or go
        print(f"[자동추정] 입력={in_cols}  출력={out_cols}")
    if not in_cols or not out_cols:
        raise SystemExit("입력/출력 컬럼을 결정할 수 없습니다. --inputs/--outputs 로 지정하세요.")

    # 행 -> 텐서
    X = torch.tensor([[float(r[c]) for c in in_cols] for r in rows], dtype=torch.float32)
    Y = torch.tensor([[float(r[c]) for c in out_cols] for r in rows], dtype=torch.float32)

    x_mean, x_std = X.mean(0), X.std(0).clamp_min(1e-6)
    y_mean, y_std = Y.mean(0), Y.std(0).clamp_min(1e-6)
    Xn = (X - x_mean) / x_std
    Yn = (Y - y_mean) / y_std

    device = args.device
    hidden = [int(h) for h in args.hidden.split(",")]
    model = AngleMLP(len(in_cols), len(out_cols), hidden).to(device)
    Xn, Yn = Xn.to(device), Yn.to(device)

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()
    n = Xn.shape[0]

    print(f"학습 시작: device={device}, samples={n}, in={len(in_cols)}, out={len(out_cols)}")
    for epoch in range(args.epochs):
        perm = torch.randperm(n, device=device)
        total = 0.0
        for i in range(0, n, args.batch):
            idx = perm[i:i + args.batch]
            opt.zero_grad()
            pred = model(Xn[idx])
            loss = loss_fn(pred, Yn[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        if (epoch + 1) % max(1, args.epochs // 10) == 0:
            print(f"  epoch {epoch+1:4d}/{args.epochs}  loss={total/n:.5f}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model_state": model.state_dict(),
        "in_features": in_cols,
        "out_features": out_cols,
        "x_mean": x_mean.tolist(), "x_std": x_std.tolist(),
        "y_mean": y_mean.tolist(), "y_std": y_std.tolist(),
        "hidden": hidden,
    }, out_path)
    print(f"저장 완료: {out_path}")


if __name__ == "__main__":
    main()
