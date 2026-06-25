"""학습된 커스텀 YOLO(best.pt) 를 test 셋 이미지에 적용해 검출을 확인한다.

실행: .venv\\Scripts\\python -m tools.check_yolo
"""
import glob

import cv2

from app.config import config
from app.vision.detector import Detector

TEST_GLOB = "data/targets/test/images/*.jpg"


def main() -> None:
    det = Detector()  # models/yolo/best.pt 자동 로드
    paths = sorted(glob.glob(TEST_GLOB))[:10]
    if not paths:
        raise SystemExit(f"test 이미지를 찾을 수 없습니다: {TEST_GLOB}")
    print(f"device={det.device}, conf>={det.conf}, classes={det.names}")
    total = 0
    for p in paths:
        frame = cv2.imread(p)
        dets = det.detect(frame)
        total += len(dets)
        confs = ", ".join(f"{d.label}:{d.conf:.2f}" for d in dets) or "(없음)"
        print(f"  {p.split('/')[-1][:40]:42s} -> {len(dets)}건  [{confs}]")
    print(f"총 {len(paths)}장에서 검출 {total}건")


if __name__ == "__main__":
    main()
