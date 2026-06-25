"""원하는 서보를 원하는 각도로 설정하는 도구 (Pi 에서 실행, 서보 조립/캘리브레이션용).

프로토콜상 명령 1개에 pan/tilt/trigger 가 함께 들어가므로, 이 도구는 현재 pan/tilt 를
기억해 두고 지정한 축만 바꿔 전송한다(나머지 축은 유지). 시작 시 STM32 상태(STATUS)로
현재 각도를 동기화한다.

실행 예:
  # 한 번만 설정하고 종료
  python3 servo_set.py --port /dev/serial0 --pan 90 --tilt 90 --once
  # 대화 모드 (조립 중 각도 맞추기에 편함)
  python3 servo_set.py --port /dev/serial0
  # 트리거(발사)도 허용
  python3 servo_set.py --port /dev/serial0 --trigger

대화 모드 명령:
  pan 90      -> Pan 만 90° (Tilt 유지)
  tilt 120    -> Tilt 만 120° (Pan 유지)
  both 90 90  -> Pan/Tilt 동시
  fire        -> Trigger 발사(--trigger 필요, 120°→500ms 후 0° 복귀)
  s           -> 현재 STM32 상태 읽기
  q           -> 종료
"""
from __future__ import annotations

import argparse
import time

from stm32_link import Stm32Link


def _clamp(v: int) -> int:
    return max(0, min(180, int(v)))


def sync_status(link: Stm32Link, seconds: float = 0.6):
    """STATUS 를 잠깐 읽어 현재 (pan,tilt) 를 반환. 없으면 None."""
    last = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        st = link.read_status()
        if st is not None:
            last = st
        time.sleep(0.02)
    return last


def send_and_report(link: Stm32Link, pan: int, tilt: int, trig: int):
    link.send_command(pan, tilt, trig)
    st = sync_status(link, 0.5)
    print(f"    보냄 pan={pan} tilt={tilt} trig={trig} -> 회신 {st}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--pan", type=int, help="시작 시 Pan 각도(0~180)")
    ap.add_argument("--tilt", type=int, help="시작 시 Tilt 각도(0~180)")
    ap.add_argument("--trigger", action="store_true", help="발사(trigger) 허용")
    ap.add_argument("--once", action="store_true", help="인자대로 한 번 설정하고 종료")
    args = ap.parse_args()

    print(f"[servo_set] 포트 열기 {args.port} @ {args.baud}")
    link = Stm32Link(args.port, args.baud)
    time.sleep(0.3)

    # 현재 각도 동기화(없으면 펌웨어 기본 90/90 가정)
    st = sync_status(link, 0.8)
    cur_pan, cur_tilt = (st if st is not None else (90, 90))
    print(f"[servo_set] 현재 각도 {'(STM32 동기화)' if st else '(기본값 가정)'}: pan={cur_pan} tilt={cur_tilt}")

    # CLI 인자로 초기 설정
    if args.pan is not None:
        cur_pan = _clamp(args.pan)
    if args.tilt is not None:
        cur_tilt = _clamp(args.tilt)
    if args.pan is not None or args.tilt is not None:
        send_and_report(link, cur_pan, cur_tilt, 0)

    if args.once:
        link.close()
        print("[servo_set] 종료(--once).")
        return

    # 대화 모드
    print("\n명령: 'pan N' | 'tilt N' | 'both P T' | 'fire' | 's'(상태) | 'q'(종료)")
    if args.trigger:
        print("⚠️ --trigger 활성: 'fire' 입력 시 실제 발사 걸쇠가 동작합니다!")
    try:
        while True:
            line = input(f"[pan={cur_pan} tilt={cur_tilt}] > ").strip().lower()
            if line in ("q", "quit", "exit"):
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0]
            try:
                if cmd == "pan":
                    cur_pan = _clamp(parts[1])
                    send_and_report(link, cur_pan, cur_tilt, 0)
                elif cmd == "tilt":
                    cur_tilt = _clamp(parts[1])
                    send_and_report(link, cur_pan, cur_tilt, 0)
                elif cmd == "both":
                    cur_pan = _clamp(parts[1]); cur_tilt = _clamp(parts[2])
                    send_and_report(link, cur_pan, cur_tilt, 0)
                elif cmd in ("fire", "trigger", "shot"):
                    if not args.trigger:
                        print("    트리거 비활성 — '--trigger' 로 실행해야 발사됩니다.")
                        continue
                    send_and_report(link, cur_pan, cur_tilt, 1)
                elif cmd == "s":
                    print(f"    상태: {sync_status(link, 0.5)}")
                else:
                    print("    형식: pan N | tilt N | both P T | fire | s | q")
            except (IndexError, ValueError):
                print("    형식 오류. 예) pan 90 / tilt 120 / both 90 90")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        link.close()
        print("\n[servo_set] 종료(현재 각도 유지).")


if __name__ == "__main__":
    main()
