"""목(mock) Raspberry Pi 클라이언트.

RPi 실물 없이 PC 서버의 전체 흐름(영상->검출->조준->측거->발사->판정)을
검증하기 위한 시뮬레이터다. 서버 TCP 에 접속하여:

  - FRAME  : 합성 영상(마젠타 표적) 또는 웹캠 프레임을 주기 전송
  - STATUS : 시뮬레이션 서보 현재 각도 전송
  - LIDAR  : 표적까지 거리(mm) 전송
  - CONTROL: 서버가 보낸 목표 각도를 받아 서보를 점진 이동, trigger=1 이면 발사로 간주

옵션:
  --mode synthetic|webcam   (기본 synthetic)
  --host 127.0.0.1 --port 9000
  --auto-hit                trigger 수신 후 잠시 뒤 /api/hit 자동 호출(명중 시뮬레이션)
  --http http://127.0.0.1:8000   (--auto-hit 대상 서버)

합성 모드: 표적이 서보 각도 (world_pan, world_tilt)=(110,80) 에서 화면 중앙에 오도록
배치된다. 서버 조준이 수렴하면 표적이 십자선 중앙으로 모인다.

실행:
    .venv\\Scripts\\python -m tools.mock_rpi --mode synthetic --auto-hit
"""
from __future__ import annotations

import argparse
import socket
import struct
import threading
import time
import urllib.request

import cv2
import numpy as np

from app.comms.protocol import (
    MsgType,
    StatusPacket,
    pack_lidar,
    recv_message,
    send_message,
)

FRAME_W, FRAME_H = 1280, 720   # Pi 카메라 운용 해상도(16:9)
PX_PER_DEG = 8.0           # 카메라 환산(작게 잡아 tilt 0~45 범위가 화면 안에 들어오게)
WORLD_PAN, WORLD_TILT = 100, 25   # 이 서보 각도에서 표적이 중앙(tilt 0~45 범위 내)
SERVO_SPEED_DEG = 6.0      # 프레임당 최대 서보 이동량


class MockState:
    def __init__(self) -> None:
        self.pan = 90.0
        self.tilt = 10.0
        self.pan_target = 90.0
        self.tilt_target = 10.0
        self.trigger = 0
        self.lock = threading.Lock()


def control_reader(sock, ms: MockState, args) -> None:
    """서버의 CONTROL 메시지를 수신하여 서보 목표/트리거를 갱신."""
    while True:
        msg = recv_message(sock)
        if msg is None:
            break
        msg_type, payload = msg
        if msg_type == MsgType.CONTROL:
            pan_t, tilt_t, trig = struct.unpack("<iii", payload)
            with ms.lock:
                ms.pan_target = float(pan_t)
                ms.tilt_target = float(tilt_t)
            if trig == 1:
                print(f"[mock] 발사 명령 수신! (pan={pan_t}, tilt={tilt_t})")
                if args.auto_hit:
                    threading.Thread(target=_delayed_hit, args=(args.http,), daemon=True).start()
        elif msg_type == MsgType.RANGE_REQ:
            # 측거 요청 -> 현재 자세 기준 거리 1회 응답 (실제 Pi 트리거 모드 모사)
            with ms.lock:
                pan, tilt = ms.pan_target, ms.tilt_target
            try:
                send_message(sock, MsgType.LIDAR, pack_lidar(distance_mm(pan, tilt)))
            except OSError:
                break


def _delayed_hit(http_base: str, delay: float = 1.5) -> None:
    time.sleep(delay)
    try:
        req = urllib.request.Request(
            http_base.rstrip("/") + "/api/hit",
            data=b'{"source":"mock-esp32"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
        print("[mock] ESP32 명중 신호 전송 완료")
    except Exception as e:
        print(f"[mock] 명중 신호 전송 실패: {e}")


def step_servo(ms: MockState) -> tuple[float, float]:
    with ms.lock:
        for attr_cur, attr_tgt in (("pan", "pan_target"), ("tilt", "tilt_target")):
            cur = getattr(ms, attr_cur)
            tgt = getattr(ms, attr_tgt)
            diff = tgt - cur
            if abs(diff) > SERVO_SPEED_DEG:
                cur += SERVO_SPEED_DEG * (1 if diff > 0 else -1)
            else:
                cur = tgt
            setattr(ms, attr_cur, cur)
        return ms.pan, ms.tilt


def render_synthetic(pan: float, tilt: float) -> np.ndarray:
    """서보 각도에 따라 마젠타 표적이 이동하는 합성 프레임."""
    frame = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
    # 표적 픽셀 위치: 서보가 world 각도에 가까울수록 중앙
    # (pan 부호는 invert_pan=True 컨트롤러가 수렴하도록 맞춤)
    cx = int(FRAME_W / 2 + (pan - WORLD_PAN) * PX_PER_DEG)
    cy = int(FRAME_H / 2 + (tilt - WORLD_TILT) * PX_PER_DEG)
    cv2.circle(frame, (cx, cy), 28, (255, 0, 255), -1)  # 마젠타(BGR)
    return frame


def distance_mm(pan: float, tilt: float) -> int:
    """표적까지 거리(mm).

    탄도 MLP 학습 데이터(weight=0.003, air=0.05)의 착탄거리 범위가 0.21~0.30m 라,
    데모에서 발사각이 의미 있도록 그 범위(약 250mm) 근처로 둔다. 실제 TF-Luna 는
    RPi 펌웨어가 측정값을 mm 로 전송한다.
    """
    base = 250
    off = int((abs(WORLD_PAN - pan) + abs(WORLD_TILT - tilt)) * 0.5)
    return base + off


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["synthetic", "webcam"], default="synthetic")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--auto-hit", action="store_true")
    ap.add_argument("--http", default="http://127.0.0.1:8000")
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--heavy", action="store_true",
                    help="실사처럼 큰 JPEG(수백 KB) 전송 — 프레임 크기 이슈 진단용")
    args = ap.parse_args()

    cap = None
    if args.mode == "webcam":
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[mock] 웹캠을 열 수 없습니다. synthetic 모드로 전환합니다.")
            args.mode = "synthetic"
            cap = None

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.host, args.port))
    print(f"[mock] 서버 접속: {args.host}:{args.port} (mode={args.mode})")

    ms = MockState()
    threading.Thread(target=control_reader, args=(sock, ms, args), daemon=True).start()

    period = 1.0 / args.fps
    try:
        while True:
            pan, tilt = step_servo(ms)
            if args.mode == "webcam" and cap is not None:
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.resize(frame, (FRAME_W, FRAME_H))
                dist = 1500
            else:
                frame = render_synthetic(pan, tilt)
                dist = distance_mm(pan, tilt)
                if args.heavy:
                    # 압축이 잘 안 되는 노이즈를 더해 실사급 대용량 JPEG 생성
                    noise = (np.random.rand(FRAME_H, FRAME_W, 3) * 255).astype(np.uint8)
                    frame = cv2.addWeighted(frame, 0.5, noise, 0.5, 0)

            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                send_message(sock, MsgType.FRAME, buf.tobytes())
            send_message(sock, MsgType.STATUS, StatusPacket(int(pan), int(tilt)).pack())
            # LiDAR 는 연속 송신하지 않음 — 서버의 측거 요청(RANGE_REQ) 시에만 응답(트리거 모드)
            time.sleep(period)
    except (KeyboardInterrupt, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        sock.close()
        if cap is not None:
            cap.release()
        print("[mock] 종료")


if __name__ == "__main__":
    main()
