"""LiDAR ON/OFF 상태에서 카메라 사진을 찍어 IR 글레어를 비교한다 (Pi 에서 실행).

noir 카메라는 IR 을 받아들이므로, LiDAR(적외선)가 켜지면 화면 중앙에 글레어가 생겨
표적 검출을 방해할 수 있다. 이 도구로 두 장을 찍어 글레어 위치/세기를 눈으로 확인하고,
IR-cut 필터나 LiDAR 오프셋이 필요한지 판단한다.

실행:
  python3 lidar_glare_check.py --lidar-port /dev/ttyUSB0 --width 1280 --height 720

결과: lidar_on.jpg(IR 켠 상태), lidar_off.jpg(트리거 모드=IR 끈 상태) 저장.
같은 폴더의 lidar.py, rpi_node.py(Camera) 를 사용한다.
"""
from __future__ import annotations

import argparse
import time

import cv2

from lidar import TFLuna
from rpi_node import Camera


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lidar-port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--camera", choices=["picamera2", "opencv"], default="picamera2")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--settle", type=float, default=1.5, help="모드 전환 후 안정 대기(초)")
    args = ap.parse_args()

    print(f"[glare] 카메라 열기 {args.camera} {args.width}x{args.height}")
    cam = Camera(args.camera, args.width, args.height, 0)
    print(f"[glare] LiDAR 열기 {args.lidar_port}")
    lidar = TFLuna(args.lidar_port, args.baud)
    time.sleep(0.3)

    try:
        # 1) LiDAR ON (연속 측정 = IR 발광)
        lidar.set_continuous_mode(hz=100)
        print(f"[glare] LiDAR ON(IR 발광) — {args.settle}s 안정 대기...")
        time.sleep(args.settle)
        on = cam.capture()
        if on is not None:
            cv2.imwrite("lidar_on.jpg", on)
            print("[glare] 저장: lidar_on.jpg  (LiDAR 켠 상태)")

        # 2) LiDAR OFF (트리거 모드 = 자동 측정 안 함 = IR off)
        lidar.set_trigger_mode()
        print(f"[glare] LiDAR OFF(트리거 모드) — {args.settle}s 대기...")
        time.sleep(args.settle)
        off = cam.capture()
        if off is not None:
            cv2.imwrite("lidar_off.jpg", off)
            print("[glare] 저장: lidar_off.jpg (LiDAR 끈 상태)")
    finally:
        lidar.close()
        cam.close()

    print("\n[glare] 완료. 두 사진을 비교하세요:")
    print("  - lidar_on.jpg  : 중앙에 밝은 점/번짐(글레어)이 보이면 IR 영향 있음")
    print("  - lidar_off.jpg : 글레어가 없어야 정상")
    print("  글레어가 크면 -> IR-cut 필터 또는 LiDAR 를 광축에서 오프셋")


if __name__ == "__main__":
    main()
