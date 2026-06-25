"""LiDAR 측정 + 카메라 라이브 뷰 동시 테스트 (Pi 에서 실행).

LiDAR 를 켠 채(연속 측정 = IR 발광) 카메라 영상을 브라우저로 실시간 송출하면서,
화면 중앙 십자선과 측정 거리(mm/cm)를 영상 위에 오버레이한다. 이렇게 하면
  - LiDAR 글레어(IR 번짐)가 영상 어디에 어떻게 보이는지,
  - 거리값이 물체를 가까이/멀리 했을 때 맞게 변하는지
를 한 화면에서 동시에 눈으로 확인할 수 있다.

실행 (Pi):
  python3 lidar_cam_test.py --lidar-port /dev/ttyUSB0 --width 1280 --height 720
  → 같은 네트워크 PC/폰 브라우저에서  http://<PI_IP>:8080  접속

옵션:
  --trigger    : LiDAR 트리거 모드(평소 IR off, 0.1s 마다 1회 측정) — 글레어 적은 상태 비교용
  --no-lidar   : 카메라만(거리 표시 없음)
  --port 8080  : MJPEG 웹 서버 포트
  --fps 15     : 카메라 송출 프레임레이트
  --show       : Pi 데스크톱(모니터/VNC)에서 cv2 창도 띄움(SSH 헤드리스면 생략)

같은 폴더의 lidar.py, rpi_node.py(Camera) 를 재사용한다.  Ctrl+C 로 종료.
"""
from __future__ import annotations

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

from lidar import TFLuna
from rpi_node import Camera

# 카메라 스레드 ↔ LiDAR 스레드 ↔ HTTP 스레드가 공유하는 상태
_lock = threading.Lock()
_shared = {"jpeg": None, "mm": None, "mm_ts": 0.0, "fps": 0.0}
_running = True


# --- LiDAR 스레드: 거리 측정 -> 공유 상태 갱신 -----------------------------
def lidar_loop(lidar: TFLuna, trigger: bool) -> None:
    while _running:
        try:
            mm = lidar.measure_once_mm() if trigger else lidar.read_distance_mm()
        except Exception as e:  # noqa: BLE001
            print(f"[lidar] 읽기 오류: {e}")
            mm = None
        if mm is not None:
            with _lock:
                _shared["mm"] = mm
                _shared["mm_ts"] = time.monotonic()
        time.sleep(0.1 if trigger else 0.02)


# --- 영상 오버레이 ---------------------------------------------------------
def annotate(frame, mm, mm_age, fps, mode_label):
    h, w = frame.shape[:2]
    # 중앙 십자선(LiDAR 광축 대략 위치)
    cv2.drawMarker(frame, (w // 2, h // 2), (0, 255, 255),
                   cv2.MARKER_CROSS, 28, 2)

    if mm is None:
        dist_txt = "LiDAR: --- (수신 없음)"
        color = (80, 80, 255)
    elif mm_age > 1.0:
        dist_txt = f"LiDAR: {mm} mm  (오래됨 {mm_age:.1f}s)"
        color = (0, 180, 255)
    else:
        dist_txt = f"LiDAR: {mm/10:.1f} cm   ({mm} mm)"
        color = (0, 255, 0)

    # 가독성 위해 반투명 배경 박스
    cv2.rectangle(frame, (0, 0), (w, 64), (0, 0, 0), -1)
    cv2.putText(frame, dist_txt, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, color, 2, cv2.LINE_AA)
    cv2.putText(frame, f"{mode_label} | {fps:.0f} fps | {w}x{h}",
                (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    return frame


# --- 카메라 스레드: 캡처 -> 오버레이 -> JPEG --------------------------------
def camera_loop(cam: Camera, fps: float, quality: int, mode_label: str, show: bool) -> None:
    period = 1.0 / max(1.0, fps)
    n, t0 = 0, time.monotonic()
    cur_fps = 0.0
    while _running:
        t = time.monotonic()
        try:
            frame = cam.capture()
        except Exception as e:  # noqa: BLE001
            print(f"[cam] 캡처 오류: {e}")
            time.sleep(0.3)
            continue
        if frame is None:
            time.sleep(0.05)
            continue

        with _lock:
            mm = _shared["mm"]
            mm_age = time.monotonic() - _shared["mm_ts"] if _shared["mm_ts"] else 1e9
            cur_fps = _shared["fps"]

        frame = annotate(frame, mm, mm_age, cur_fps, mode_label)

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            with _lock:
                _shared["jpeg"] = buf.tobytes()

        if show:
            cv2.imshow("lidar_cam_test", frame)
            if cv2.waitKey(1) & 0xFF == 27:  # ESC
                break

        # FPS 측정(최근 평균)
        n += 1
        if t - t0 >= 1.0:
            with _lock:
                _shared["fps"] = n / (t - t0)
            n, t0 = 0, t

        dt = period - (time.monotonic() - t)
        if dt > 0:
            time.sleep(dt)


# --- MJPEG HTTP 서버 -------------------------------------------------------
_PAGE = ("""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LiDAR + Camera Test</title>
<style>body{margin:0;background:#0e1116;color:#e6edf3;font-family:system-ui,sans-serif;text-align:center}
img{max-width:100%;height:auto}h3{margin:10px}</style></head>
<body><h3>🎯 LiDAR + Camera 라이브 테스트</h3>
<img src="/stream"><p>중앙 십자선 = LiDAR 광축 근처 · 상단 = 측정 거리</p></body></html>""").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 콘솔 스팸 방지
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)
            return
        if self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        last = None
        try:
            while _running:
                with _lock:
                    jpeg = _shared["jpeg"]
                if jpeg is not None and jpeg is not last:
                    last = jpeg
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"Content-Length: " + str(len(jpeg)).encode() +
                                     b"\r\n\r\n" + jpeg + b"\r\n")
                time.sleep(0.04)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 브라우저 탭 닫힘 — 정상


def get_lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    global _running
    ap = argparse.ArgumentParser()
    ap.add_argument("--lidar-port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--mm", action="store_true", help="센서가 9B/mm 포맷일 때")
    ap.add_argument("--trigger", action="store_true",
                    help="LiDAR 트리거 모드(평소 IR off, 0.1s 마다 1회 측정)")
    ap.add_argument("--no-lidar", action="store_true", help="카메라만(거리 표시 없음)")
    ap.add_argument("--camera", choices=["picamera2", "opencv"], default="picamera2")
    ap.add_argument("--cam-index", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--quality", type=int, default=75, help="송출 JPEG 품질(1~100)")
    ap.add_argument("--port", type=int, default=8080, help="MJPEG 웹 서버 포트")
    ap.add_argument("--show", action="store_true", help="Pi 데스크톱이면 cv2 창도 띄움")
    args = ap.parse_args()

    print(f"[test] 카메라 열기 {args.camera} {args.width}x{args.height}")
    cam = Camera(args.camera, args.width, args.height, args.cam_index)

    lidar = None
    if not args.no_lidar:
        print(f"[test] LiDAR 열기 {args.lidar_port} (unit={'mm' if args.mm else 'cm'})")
        try:
            lidar = TFLuna(args.lidar_port, args.baud, unit_mm=args.mm)
            if args.trigger:
                lidar.set_trigger_mode()
                mode_label = "LiDAR TRIGGER(IR off, 0.1s마다 측정)"
            else:
                lidar.set_continuous_mode(hz=100)
                mode_label = "LiDAR ON(연속, IR 발광)"
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            print(f"    ⚠️ LiDAR 열기 실패 — 카메라만 진행: {e}")
            lidar, mode_label = None, "LiDAR 없음"
    else:
        mode_label = "LiDAR 비활성(--no-lidar)"

    # 스레드 시작
    threads = [threading.Thread(target=camera_loop,
                                args=(cam, args.fps, args.quality, mode_label, args.show),
                                daemon=True)]
    if lidar is not None:
        threads.append(threading.Thread(target=lidar_loop, args=(lidar, args.trigger), daemon=True))
    for t in threads:
        t.start()

    ip = get_lan_ip()
    print("\n================ LiDAR + Camera 테스트 ================")
    print(f"  브라우저로 접속:  http://{ip}:{args.port}")
    print(f"  (로컬:           http://127.0.0.1:{args.port})")
    print(f"  모드: {mode_label}")
    print("  종료: Ctrl+C")
    print("======================================================\n")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    # 콘솔에도 거리 주기 출력(브라우저 없이 SSH 만으로도 확인 가능)
    def console_printer():
        last = 0.0
        while _running:
            now = time.monotonic()
            if now - last >= 0.5:
                last = now
                with _lock:
                    mm, fps = _shared["mm"], _shared["fps"]
                if lidar is not None:
                    txt = f"{mm/10:6.1f} cm ({mm} mm)" if mm is not None else "--- (수신 없음)"
                    print(f"    거리: {txt}   |  {fps:4.1f} fps")
            time.sleep(0.1)
    threading.Thread(target=console_printer, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        server.shutdown()
        time.sleep(0.2)
        if lidar is not None:
            # 다음 사용을 위해 연속 모드로 복귀시켜 두면 편함(트리거로 바꿔 놨을 수 있음)
            try:
                lidar.close()
            except Exception:  # noqa: BLE001
                pass
        cam.close()
        if args.show:
            cv2.destroyAllWindows()
        print("\n[test] 종료.")


if __name__ == "__main__":
    main()
