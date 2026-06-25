"""Raspberry Pi 5 통신 노드.

역할(시스템 개요의 RPi 파트):
  - Pi Camera 프레임 캡처 -> PC 서버로 FRAME 전송
  - TF-Luna LiDAR 거리 측정 -> PC 로 LIDAR(mm) 전송
  - STM32 와 UART: PC 의 CONTROL 을 STM32 명령으로 중계, STM32 상태를 PC 로 STATUS 전송
  - PC 와 TCP (PC 가 서버, Pi 가 클라이언트). 끊기면 자동 재연결.

실행:
  python3 rpi_node.py --pc-host 192.168.0.10 --pc-port 9000 \
      --stm32-port /dev/ttyAMA0 --lidar-port /dev/ttyUSB0 \
      --camera picamera2 --width 1280 --height 720 --fps 20

의존성(Pi): pip install pyserial opencv-python ; picamera2(라즈베리파이 OS 기본 제공)
"""
from __future__ import annotations

import argparse
import logging
import socket
import threading
import time

import cv2

from protocol import (
    ControlPacket,
    MsgType,
    StatusPacket,
    pack_lidar,
    recv_message,
    send_message,
    unpack_motor,
)
from stm32_link import Stm32Link
from lidar import TFLuna
from motor_hat import MotorHat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("rpi_node")


# --- 카메라 소스 --------------------------------------------------------
class Camera:
    """picamera2 또는 OpenCV(USB) 카메라 래퍼. capture()->BGR ndarray."""

    def __init__(self, kind: str, width: int, height: int, index: int = 0) -> None:
        self.kind = kind
        self.width, self.height = width, height
        if kind == "picamera2":
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            cfg = self.picam2.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"})
            self.picam2.configure(cfg)
            self.picam2.start()
            time.sleep(0.5)
        else:
            self.cap = cv2.VideoCapture(index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def capture(self):
        if self.kind == "picamera2":
            arr = self.picam2.capture_array()
            # picamera2 'RGB888' 은 numpy 에서 실제 BGR 바이트 순서(OpenCV 호환)다.
            # 따라서 추가 색 변환을 하면 R↔B 가 뒤바뀌므로 변환하지 않는다.
            # 4채널(XBGR8888)이면 앞 3채널(BGR)만 사용.
            if arr.ndim == 3 and arr.shape[2] == 4:
                arr = arr[:, :, :3]
            return arr  # 이미 OpenCV BGR
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        if self.kind == "picamera2":
            self.picam2.stop()
        else:
            self.cap.release()


# --- 노드 ---------------------------------------------------------------
class RpiNode:
    def __init__(self, args) -> None:
        self.args = args
        self.sock = None
        self.send_lock = threading.Lock()
        self.running = False

        self.camera = Camera(args.camera, args.width, args.height, args.cam_index)
        # 센서는 옵션으로 비활성 가능(YOLO/카메라만 테스트할 때)
        self.stm32 = None if args.no_stm32 else Stm32Link(args.stm32_port, args.baud)
        self.lidar = None if args.no_lidar else TFLuna(args.lidar_port, args.baud, unit_mm=args.lidar_mm)
        # HAT(DC 모터 + 서보): 웹에서 MOTOR 메시지로 제어. 라이브러리/HW 없으면 mock.
        self.motor = None if args.no_motor else MotorHat(
            addr=int(args.motor_addr, 16),
            dc_channel=args.dc_channel,
            servo_channel=args.servo_channel,
        )
        if self.stm32 is None:
            logger.info("STM32 비활성(--no-stm32): CONTROL 중계 안 함")
        if self.lidar is None:
            logger.info("LiDAR 비활성(--no-lidar): 거리 전송 안 함")
        else:
            # 트리거 모드: 평소 측정 안 함(IR off) → 측거 요청(RANGE_REQ) 시에만 1회 측정
            try:
                self.lidar.set_trigger_mode()
                logger.info("LiDAR 트리거 모드 설정(조준 중 IR off, 측거 요청 시에만 측정)")
            except Exception as e:  # noqa: BLE001
                logger.warning("LiDAR 트리거 모드 설정 실패(연속 모드로 진행): %s", e)

    # --- PC 송신(스레드 안전) ---
    def _send(self, msg_type: MsgType, payload: bytes) -> bool:
        with self.send_lock:
            if self.sock is None:
                return False
            try:
                send_message(self.sock, msg_type, payload)
                return True
            except OSError as e:
                logger.warning("PC 송신 실패: %s", e)
                return False

    # --- 스레드: 카메라 -> FRAME ---
    def _camera_loop(self):
        period = 1.0 / self.args.fps
        sent = 0
        while self.running:
            t0 = time.monotonic()
            try:
                frame = self.camera.capture()
            except Exception as e:  # noqa: BLE001
                logger.exception("카메라 캡처 오류: %s", e)
                time.sleep(0.5)
                continue
            if frame is None:
                logger.warning("프레임이 None (캡처 실패)")
                time.sleep(0.1)
                continue
            if sent == 0:
                logger.info("첫 프레임 캡처 OK: shape=%s dtype=%s", frame.shape, frame.dtype)
            t1 = time.monotonic()
            ok, buf = cv2.imencode(".jpg", frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, self.args.jpeg_quality])
            if not ok:
                logger.warning("JPEG 인코딩 실패")
                continue
            t2 = time.monotonic()
            if not self._send(MsgType.FRAME, buf.tobytes()):
                logger.warning("FRAME 전송 실패(연결 끊김) -> 재연결")
                return  # 연결 끊김 -> 스레드 종료(재연결 트리거)
            t3 = time.monotonic()
            sent += 1
            # 단계별 소요시간(ms) — 어디가 느린지 진단 (capture / encode / send)
            if sent <= 8 or sent % 60 == 0:
                logger.info("타이밍: capture=%.0fms encode=%.0fms send=%.0fms (%d bytes)",
                            (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, len(buf))
            if sent <= 5 or sent % 60 == 0:
                logger.info("FRAME 전송 누적 %d장", sent)
            time.sleep(period)

    # --- 스레드: LiDAR -> LIDAR, STM32 상태 -> STATUS ---
    def _sensor_loop(self):
        # STM32 상태만 주기 폴링. LiDAR 는 트리거 모드라 측거 요청(RANGE_REQ) 시에만 측정한다.
        if self.stm32 is None:
            return  # 보낼 상태 없음 -> 스레드 종료
        while self.running:
            st = self.stm32.read_status()
            if st is not None:
                if not self._send(MsgType.STATUS, StatusPacket(st[0], st[1]).pack()):
                    return
            time.sleep(0.01)

    # --- PC CONTROL 수신 -> STM32 중계 ---
    def _control_loop(self):
        while self.running:
            try:
                msg = recv_message(self.sock)
            except OSError:
                msg = None
            if msg is None:
                return  # 연결 끊김
            msg_type, payload = msg
            if msg_type == MsgType.CONTROL:
                pkt = ControlPacket.unpack(payload)
                if self.stm32 is not None:
                    self.stm32.send_command(pkt.pan_target, pkt.tilt_target, pkt.trigger)
                logger.info("CONTROL%s: pan=%d tilt=%d trig=%d",
                            " -> STM32" if self.stm32 else "(무시:STM32 비활성)",
                            pkt.pan_target, pkt.tilt_target, pkt.trigger)
            elif msg_type == MsgType.RANGE_REQ:
                # 측거 요청: LiDAR 트리거(1회 측정 = 이때만 IR 발광) 후 거리 송신
                if self.lidar is not None:
                    dist = self.lidar.measure_once_mm()
                    if dist is not None:
                        self._send(MsgType.LIDAR, pack_lidar(int(dist)))
                        logger.info("측거 요청 -> %d mm", int(dist))
                    else:
                        logger.warning("측거 실패(LiDAR 응답 없음)")
                else:
                    logger.info("측거 요청(무시: LiDAR 비활성)")
            elif msg_type == MsgType.MOTOR:
                # 웹에서 온 HAT 모터 명령(DC/서보) 처리
                cmd = unpack_motor(payload)
                kind = cmd.get("kind")
                if self.motor is None:
                    logger.info("MOTOR(무시: HAT 비활성): %s", cmd)
                elif kind == "dc":
                    self.motor.dc(cmd.get("dir", "stop"), int(cmd.get("speed", 100)))
                    logger.info("MOTOR DC: dir=%s speed=%s", cmd.get("dir"), cmd.get("speed"))
                elif kind == "servo":
                    self.motor.servo(int(cmd.get("val", 350)))
                    logger.info("MOTOR SERVO: val=%s", cmd.get("val"))
                else:
                    logger.warning("알 수 없는 MOTOR 명령: %s", cmd)

    # --- 연결 1회 세션 ---
    def _session(self):
        self.running = True
        threads = [
            threading.Thread(target=self._camera_loop, daemon=True),
            threading.Thread(target=self._sensor_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        # control 수신은 현재 스레드에서(끊기면 반환)
        self._control_loop()
        self.running = False
        for t in threads:
            t.join(timeout=1.0)

    # --- 메인 루프(재연결) ---
    def run(self):
        while True:
            try:
                logger.info("PC 접속 시도: %s:%d", self.args.pc_host, self.args.pc_port)
                self.sock = socket.create_connection(
                    (self.args.pc_host, self.args.pc_port), timeout=5)
                self.sock.settimeout(None)
                logger.info("PC 연결 성공")
                self._session()
            except (OSError, KeyboardInterrupt) as e:
                logger.warning("연결 종료/실패: %s", e)
            finally:
                with self.send_lock:
                    if self.sock:
                        try:
                            self.sock.close()
                        except OSError:
                            pass
                    self.sock = None
            time.sleep(2.0)  # 재연결 대기

    def close(self):
        self.camera.close()
        if self.stm32 is not None:
            self.stm32.close()
        if self.lidar is not None:
            self.lidar.close()
        if self.motor is not None:
            self.motor.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc-host", required=True, help="PC 서버 IP")
    ap.add_argument("--pc-port", type=int, default=9000)
    ap.add_argument("--stm32-port", default="/dev/ttyAMA0")
    ap.add_argument("--lidar-port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--lidar-mm", action="store_true", help="TF-Luna 가 mm 포맷일 때")
    ap.add_argument("--no-stm32", action="store_true", help="STM32 없이 실행(중계 안 함)")
    ap.add_argument("--no-lidar", action="store_true", help="LiDAR 없이 실행(거리 전송 안 함)")
    ap.add_argument("--no-motor", action="store_true", help="HAT 모터 없이 실행(웹 모터 제어 끔)")
    ap.add_argument("--motor-addr", default="0x6f", help="Motor HAT I2C 주소(16진수)")
    ap.add_argument("--dc-channel", type=int, default=2, help="DC 모터 채널(getMotor 번호)")
    ap.add_argument("--servo-channel", type=int, default=0, help="서보 PWM 채널")
    ap.add_argument("--camera", choices=["picamera2", "opencv"], default="picamera2")
    ap.add_argument("--cam-index", type=int, default=0)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--jpeg-quality", type=int, default=55,
                    help="전송 JPEG 품질(1~100). 낮출수록 대역폭↓(끊김 완화)")
    args = ap.parse_args()

    node = RpiNode(args)
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        node.close()


if __name__ == "__main__":
    main()
