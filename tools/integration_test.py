"""엔드투엔드 통합 테스트 (HTTP API 드라이브).

전제: 서버(app.main)와 목 RPi(tools.mock_rpi --auto-hit)가 이미 실행 중.
흐름:
  1) RPi 연결 + 검출 등장 대기
  2) 검출 #0 표적 선택
  3) 발사 시퀀스 시작(/api/engage)
  4) phase==result 까지 폴링, 결과(HIT/MISS) 출력

실행: .venv\\Scripts\\python -m tools.integration_test
종료코드 0=HIT, 1=그 외(테스트 실패).
"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"


def get_state(client):
    return client.get(f"{BASE}/api/state", timeout=5).json()


def main() -> int:
    with httpx.Client() as client:
        # 1) 연결 + 검출 대기
        print("[test] RPi 연결 + 검출 대기...")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            s = get_state(client)
            if s["rpi_connected"] and s["detections"]:
                break
            time.sleep(0.3)
        else:
            print("[test] 실패: RPi 연결 또는 검출이 없음")
            return 1
        print(f"[test] 연결됨. 검출 {len(s['detections'])}건, "
              f"프레임 {s['frame_width']}x{s['frame_height']}")

        # 2) 표적 선택
        det_id = s["detections"][0]["id"]
        r = client.post(f"{BASE}/api/select/{det_id}", timeout=5).json()
        print(f"[test] 표적 선택 #{det_id}: {r}")
        assert r["ok"], "표적 선택 실패"

        # 3) 발사 시퀀스 시작
        r = client.post(f"{BASE}/api/engage", timeout=5)
        print(f"[test] engage: {r.status_code} {r.json()}")
        assert r.status_code == 200, "engage 실패"

        # 4) 결과 대기
        print("[test] 시퀀스 진행 관찰...")
        deadline = time.monotonic() + 30
        last_phase = None
        while time.monotonic() < deadline:
            s = get_state(client)
            if s["phase"] != last_phase:
                last_phase = s["phase"]
                print(f"      phase={s['phase']:8s} pan/tilt={s['pan_current']}/{s['tilt_current']} "
                      f"dist={s['distance_mm']} msg={s['message']}")
            if s["phase"] == "result":
                print(f"[test] 최종 결과: {s['result']}")
                return 0 if s["result"] == "HIT" else 1
            time.sleep(0.2)
        print("[test] 실패: 시간 내 결과 미도달")
        return 1


if __name__ == "__main__":
    sys.exit(main())
