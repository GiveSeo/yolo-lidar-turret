"""탄도 캘리브레이션: 특정 tilt 각도로 발사하고 실측 낙하거리를 기록한다 (PC).

서버의 /api/test_fire 로 지정 각도 발사 → 줄자로 잰 낙하거리를 입력 → CSV 누적.
모은 (angle, landing_distance) 로 해석식 파라미터(spring_k/spring_x/launch_height)를
보정한다(app.aiming.ballistic_solver 의 표가 실측과 맞도록).

선행: PC 서버 실행 + Pi/STM32 연결(서보·발사 가능). 안전 거리 확보 후 진행!

실행:
  .venv\\Scripts\\python -m tools.calib_fire
옵션:
  --url http://127.0.0.1:8000   서버 주소
  --out data/angle/measured.csv 기록 CSV
  --pan 90                      발사 pan(기본 정면)
  --settle 0.6                  발사 전 서보 정착 대기(초)
  --weight 0.003 --air 0.05     CSV 에 함께 기록할 발사체 스펙

대화:
  tilt 각도 입력(예: 20) → 발사 → 거리(m) 입력(예: 0.85) → 다음 각도 …
  거리 칸에서 엔터  = 같은 각도 재발사
  거리 칸에서 's'   = 이번 발사 기록 건너뜀
  각도 칸에서 'q'   = 종료
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

HEADER = ["weight", "air_resistance", "angle", "landing_distance"]


def post_json(url: str, body: dict, timeout: float = 8.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "reason": f"연결 실패: {e}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="data/angle/measured.csv")
    ap.add_argument("--pan", type=int, default=90)
    ap.add_argument("--settle", type=float, default=0.6)
    ap.add_argument("--weight", type=float, default=0.003)
    ap.add_argument("--air", type=float, default=0.05)
    args = ap.parse_args()

    fire_url = args.url.rstrip("/") + "/api/test_fire"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()

    print("============== 탄도 캘리브레이션 ==============")
    print(f"  발사 API : {fire_url}")
    print(f"  기록 CSV : {out.resolve()}")
    print(f"  pan={args.pan}  settle={args.settle}s  weight={args.weight}kg  air={args.air}")
    print("  ⚠️ 전방 안전 거리 확보! 각도 입력 시 즉시 발사됩니다.")
    print("  각도 'q'=종료 / 거리 엔터=재발사 / 거리 's'=건너뜀")
    print("==============================================\n")

    f = open(out, "a", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(f, fieldnames=HEADER)
    if new_file:
        writer.writeheader()
        f.flush()

    saved = 0
    try:
        while True:
            raw = input("tilt 각도(°) [q=종료] > ").strip()
            if raw.lower() in ("q", "quit", "exit"):
                break
            try:
                tilt = int(float(raw))
            except ValueError:
                print("  숫자로 입력하세요 (예: 20)")
                continue

            while True:  # 같은 각도 재발사 루프
                res = post_json(fire_url, {"tilt": tilt, "pan": args.pan, "settle": args.settle})
                if not res.get("ok"):
                    print(f"  ❌ 발사 실패: {res.get('reason', '오류')}")
                    break
                print(f"  💥 발사: pan={res.get('pan')} tilt={res.get('tilt')}")
                d = input("    실측 낙하거리 m [엔터=재발사, s=건너뜀] > ").strip()
                if d == "":
                    continue  # 재발사
                if d.lower() == "s":
                    break
                try:
                    dist = float(d)
                except ValueError:
                    print("    숫자(m)로 입력하세요 (예: 0.85). 이번 건 건너뜁니다.")
                    break
                writer.writerow({"weight": args.weight, "air_resistance": args.air,
                                 "angle": tilt, "landing_distance": round(dist, 3)})
                f.flush()
                saved += 1
                print(f"    ✅ 기록: tilt={tilt}° -> {dist} m  (누적 {saved}개)")
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        f.close()
        print(f"\n[calib] 종료. 총 {saved}개 측정 -> {out.resolve()}")
        if saved:
            print("  다음: 이 측정값에 맞게 spring_k/spring_x/launch_height 를 조정하고")
            print("        python -m app.aiming.ballistic_solver 로 표가 일치하는지 확인")


if __name__ == "__main__":
    main()
