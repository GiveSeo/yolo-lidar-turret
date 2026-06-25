"""Pi <-> STM32 UART 통신 단독 점검 도구 (Pi 에서 실행).

서버/카메라 없이 STM32 와의 통신만 확인한다:
  1) 수신 점검 : STM32 가 0x55 상태(4B)를 보내오는지 (양방향 확인)
  2) 서보 점검 : pan/tilt 를 여러 각도로 보내 서보가 실제로 움직이는지 + 상태 회신 확인
  3) 대화 모드 : "pan tilt [trigger]" 직접 입력해 보내기

실행:
  python3 stm32_test.py --port /dev/ttyAMA0           # 안전(트리거 비활성)
  python3 stm32_test.py --port /dev/ttyAMA0 --trigger # 대화모드에서 트리거도 허용

같은 폴더의 stm32_link.py 를 사용한다.
"""
from __future__ import annotations

import argparse
import time

from stm32_link import Stm32Link


def drain_status(link: Stm32Link, seconds: float):
    """seconds 동안 상태를 읽어 마지막 (pan,tilt) 와 수신 횟수를 반환."""
    last = None
    count = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        st = link.read_status()
        if st is not None:
            last = st
            count += 1
        time.sleep(0.02)
    return last, count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyAMA0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trigger", action="store_true", help="대화모드에서 트리거(발사) 허용")
    args = ap.parse_args()

    print(f"[stm32_test] 포트 열기 {args.port} @ {args.baud}")
    link = Stm32Link(args.port, args.baud)
    time.sleep(0.3)

    # 1) 수신 점검 -------------------------------------------------------
    print("\n[1] STM32 -> Pi 상태 수신 점검 (2초)...")
    last, count = drain_status(link, 2.0)
    if count > 0:
        print(f"    ✅ 상태 수신 OK: {count}건, 최근 pan/tilt = {last}")
    else:
        print("    ⚠️ 상태 미수신. 확인: STM32 TX(PA2)→Pi RX(GPIO15), 공통 GND,")
        print("       펌웨어 send_status 호출, baud 115200, 포트(/dev/ttyAMA0) 활성화")

    # 2) 서보 점검 -------------------------------------------------------
    print("\n[2] 서보 이동 점검 — 각 자세에서 서보가 움직이는지 눈으로 확인하세요")
    poses = [(90, 90), (45, 90), (135, 90), (90, 45), (90, 135), (90, 90)]
    for pan, tilt in poses:
        link.send_command(pan, tilt, trigger=0)
        print(f"    보냄 pan={pan} tilt={tilt} ...", end="", flush=True)
        last, count = drain_status(link, 0.8)
        if count > 0:
            print(f" 회신 {last} (수신 {count})")
        else:
            print(" (상태 회신 없음)")
    print("    → 서보가 각도대로 움직였으면 명령 전송 OK")

    # 3) 대화 모드 -------------------------------------------------------
    print("\n[3] 대화 모드: 'pan tilt' 또는 'pan tilt trigger' 입력 (q 종료)")
    if args.trigger:
        print("    ⚠️ --trigger 활성: trigger=1 입력 시 실제 발사 걸쇠가 동작합니다!")
    else:
        print("    (트리거는 비활성. 허용하려면 --trigger 로 실행)")
    try:
        while True:
            line = input("> ").strip()
            if line.lower() in ("q", "quit", "exit"):
                break
            if not line:
                continue
            parts = line.split()
            try:
                pan = int(parts[0]); tilt = int(parts[1])
                trig = int(parts[2]) if len(parts) > 2 else 0
            except (ValueError, IndexError):
                print("    형식: pan tilt [trigger]  예) 90 120  또는  90 120 1")
                continue
            if trig and not args.trigger:
                print("    트리거 비활성 상태 — 0 으로 전송합니다 (--trigger 로 허용)")
                trig = 0
            link.send_command(pan, tilt, trig)
            last, count = drain_status(link, 0.6)
            print(f"    보냄 pan={pan} tilt={tilt} trig={trig} -> 회신 {last} (수신 {count})")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        # 종료 전 중립 복귀
        link.send_command(90, 90, 0)
        link.close()
        print("\n[stm32_test] 종료(중립 복귀).")


if __name__ == "__main__":
    main()
