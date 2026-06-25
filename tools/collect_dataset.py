"""학습용 카메라 프레임 수집기 (PC, 실행 중인 서버에서).

서버의 /api/snapshot(주석 없는 '원본' 프레임)을 주기적으로 받아 이미지로 저장한다.
video_feed 는 검출 박스/십자선이 그려져 라벨링에 부적합하므로 원본을 쓴다.
이렇게 모은 이미지를 Roboflow 등으로 라벨링한 뒤 data/targets/{train,valid}/ 에 넣어
train.train_yolo 로 재학습한다.

선행 조건: PC 서버 실행 + Pi(카메라) 연결 (실제 카메라 프레임이 들어와야 함).
DETECTOR=demo 여도 원본 프레임은 그대로 저장되므로 수집 자체는 가능하다.

실행:
  .venv\\Scripts\\python -m tools.collect_dataset --interval 1.0 --out data/captures
옵션:
  --url http://127.0.0.1:8000  서버 주소
  --interval 1.0               저장 시도 간격(초)
  --count 0                    최대 저장 장수(0=무한, Ctrl+C 종료)
  --min-diff 8.0               직전 저장본과 평균 픽셀차가 이 값 미만이면 건너뜀(중복 방지, 0=항상 저장)
  --prefix cam                 파일명 접두사
  --quality 90                 (참고) 서버가 인코딩하는 품질은 서버측 고정. 여기선 저장만.
"""
from __future__ import annotations

import argparse
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def fetch_jpeg(url: str, timeout: float = 5.0):
    """서버에서 원본 스냅샷(JPEG 바이트)을 가져온다. 실패 시 None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return None
            return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def small_gray(jpeg: bytes):
    """중복 판정용: JPEG -> 64x64 그레이스케일 축소 이미지."""
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (64, 64)).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="서버 주소")
    ap.add_argument("--out", default="data/captures", help="저장 폴더")
    ap.add_argument("--interval", type=float, default=1.0, help="저장 시도 간격(초)")
    ap.add_argument("--count", type=int, default=0, help="최대 저장 장수(0=무한)")
    ap.add_argument("--min-diff", type=float, default=8.0,
                    help="직전 저장본과 평균차 이 값 미만이면 건너뜀(0=항상 저장)")
    ap.add_argument("--prefix", default="cam", help="파일명 접두사")
    args = ap.parse_args()

    snap_url = args.url.rstrip("/") + "/api/snapshot"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("================ 데이터셋 수집 ================")
    print(f"  소스 : {snap_url}")
    print(f"  저장 : {out.resolve()}")
    print(f"  간격 : {args.interval}s | 최대 : {'무한' if args.count == 0 else args.count}장 | "
          f"중복임계 : {args.min_diff}")
    print("  종료 : Ctrl+C")
    print("==============================================\n")

    saved = 0
    prev_small = None
    no_frame_warned = False
    session = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        while args.count == 0 or saved < args.count:
            t0 = time.monotonic()
            jpeg = fetch_jpeg(snap_url)

            if jpeg is None:
                if not no_frame_warned:
                    print("  ⚠️ 프레임 없음/서버 응답 없음 — 서버 실행 + Pi 카메라 연결 확인 (재시도 중)")
                    no_frame_warned = True
            else:
                no_frame_warned = False
                cur = small_gray(jpeg)
                if cur is None:
                    pass  # 디코드 실패 — 건너뜀
                else:
                    diff = 999.0 if prev_small is None else float(np.mean(np.abs(cur - prev_small)))
                    if diff >= args.min_diff:
                        saved += 1
                        prev_small = cur
                        fname = f"{args.prefix}_{session}_{saved:05d}.jpg"
                        (out / fname).write_bytes(jpeg)
                        print(f"  [{saved}] 저장 {fname}  ({len(jpeg)//1024} KB, diff={diff:.1f})")
                    # diff < 임계: 거의 같은 장면이라 건너뜀(조용히)

            # 간격 유지
            dt = args.interval - (time.monotonic() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        pass

    print(f"\n[collect] 종료. 총 {saved}장 저장 -> {out.resolve()}")
    print("  다음 단계: 라벨링(Roboflow 등) 후 data/targets/{train,valid}/ 에 넣고")
    print("            python -m train.train_yolo --imgsz 1280 로 재학습")


if __name__ == "__main__":
    main()
