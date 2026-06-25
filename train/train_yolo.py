"""커스텀 표적 YOLOv8 학습.

라벨링 완료된 데이터셋(data.yaml: images/labels, names)으로 YOLOv8 을 학습한다.
RTX 4060 (8GB) 기준 yolov8n/s 권장.

실행 예:
    .venv\\Scripts\\python -m train.train_yolo \\
        --data data/targets/data.yaml --model yolov8n.pt \\
        --epochs 100 --imgsz 640 --batch 16

학습 후 best.pt 를 models/yolo/best.pt 로 복사한다(서버가 자동 로드).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app.config import TARGETS_DATA_YAML, YOLO_WEIGHTS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(TARGETS_DATA_YAML), help="데이터셋 data.yaml 경로")
    ap.add_argument("--model", default="yolov8n.pt", help="기반 모델(전이학습 시작점)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0", help="'0'=GPU, 'cpu'")
    ap.add_argument("--name", default="target", help="실행 이름(runs/detect/<name>)")
    ap.add_argument("--workers", type=int, default=8,
                    help="데이터로더 워커 수. Windows 에서 검증 중 데드락이 나면 0 으로(메인 프로세스 로딩)")
    args = ap.parse_args()

    from ultralytics import YOLO

    if not Path(args.data).exists():
        raise SystemExit(f"data.yaml 을 찾을 수 없습니다: {args.data}")

    model = YOLO(args.model)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        name=args.name,
        workers=args.workers,
    )

    # best.pt 위치 탐색 후 models/yolo/best.pt 로 복사
    save_dir = Path(results.save_dir) if hasattr(results, "save_dir") else Path("runs/detect") / args.name
    best = save_dir / "weights" / "best.pt"
    if best.exists():
        YOLO_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(best, YOLO_WEIGHTS)
        print(f"best.pt 복사 완료 -> {YOLO_WEIGHTS}")
    else:
        print(f"경고: best.pt 를 찾지 못함({best}). 수동으로 복사하세요.")


if __name__ == "__main__":
    main()
