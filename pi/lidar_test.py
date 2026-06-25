"""Pi <-> TF-Luna LiDAR UART 단독 점검 도구 (Pi 에서 실행).

서버/STM32 없이 거리 센서만 확인한다:
  1) 수신 점검 : TF-Luna 가 유효 거리 프레임을 보내오는지 (2초)
  2) 실시간 모니터 : 거리(mm/cm)를 계속 출력 — 손/물체를 가까이/멀리 하며 값이 변하는지 확인

실행:
  python3 lidar_test.py --port /dev/ttyUSB0            # CP2102 USB 어댑터 기본
  python3 lidar_test.py --port /dev/ttyUSB0 --mm       # 센서가 mm 출력 모드일 때
  python3 lidar_test.py --port /dev/ttyUSB0 --seconds 0  # 무한(Ctrl+C 종료)

같은 폴더의 lidar.py 를 사용한다. 포트 확인: ls /dev/ttyUSB*
"""
from __future__ import annotations

import argparse
import time

from lidar import TFLuna


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--mm", action="store_true", help="센서가 9B/mm 포맷일 때")
    ap.add_argument("--seconds", type=float, default=20.0, help="실시간 모니터 시간(0=무한)")
    args = ap.parse_args()

    print(f"[lidar_test] 포트 열기 {args.port} @ {args.baud} (unit={'mm' if args.mm else 'cm'})")
    try:
        lidar = TFLuna(args.port, args.baud, unit_mm=args.mm)
    except Exception as e:  # noqa: BLE001
        print(f"    ❌ 포트 열기 실패: {e}")
        print("    확인: ls /dev/ttyUSB*  (CP2102 인식?), 배선(TF-Luna TX→어댑터 RX), 5V 전원, baud")
        return
    time.sleep(0.2)

    # 1) 수신 점검 ------------------------------------------------------
    print("\n[1] 거리 프레임 수신 점검 (2초)...")
    got = None
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        d = lidar.read_distance_mm()
        if d is not None:
            got = d
        time.sleep(0.02)
    if got is not None:
        print(f"    ✅ 수신 OK: 거리 = {got} mm ({got/10:.1f} cm)")
    else:
        print("    ⚠️ 유효 거리 미수신. 확인:")
        print("       - ls /dev/ttyUSB*  로 포트(/dev/ttyUSB0) 존재 여부")
        print("       - 배선: TF-Luna TX → CP2102 RX, TF-Luna RX → CP2102 TX, 5V/GND 공통")
        print("       - 신호강도 부족(너무 가깝거나 검은/거울 표면)일 수도 → 50cm~2m 흰 벽 향해 재시도")
        print("       - 센서가 mm 모드면 --mm, baud 기본 115200")

    # 2) 실시간 모니터 --------------------------------------------------
    print("\n[2] 실시간 거리 모니터 — 손/물체를 움직이며 값이 변하는지 확인 (Ctrl+C 종료)")
    t_end = None if args.seconds == 0 else time.monotonic() + args.seconds
    last_print = 0.0
    try:
        while t_end is None or time.monotonic() < t_end:
            d = lidar.read_distance_mm()
            now = time.monotonic()
            if now - last_print >= 0.2:   # ~5Hz 출력
                last_print = now
                if d is not None:
                    print(f"    거리: {d:5d} mm  ({d/10:6.1f} cm)")
                else:
                    print("    거리: --- (수신 없음)")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        lidar.close()
        print("\n[lidar_test] 종료.")


if __name__ == "__main__":
    main()
