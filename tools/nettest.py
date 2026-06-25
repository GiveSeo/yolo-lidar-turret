"""순수 TCP 처리량 테스트 (앱 코드와 무관하게 Pi<->PC 네트워크만 점검).

PC(수신):   python -m tools.nettest --server --port 9999
Pi(송신):   python3 nettest.py --client <PC_IP> --port 9999 --seconds 10

서버는 초당 수신 MB/s 를 출력한다. 0 에 가깝거나 멈추면 네트워크(Wi-Fi)가 데이터를
못 나르는 것 (앱이 아니라 망 문제). 정상이면 수 MB/s 가 찍힌다.
"""
from __future__ import annotations

import argparse
import socket
import time


def run_server(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(1)
    print(f"[nettest] 수신 대기 0.0.0.0:{port}")
    c, addr = s.accept()
    print(f"[nettest] 접속: {addr}")
    total = 0
    win = 0
    t0 = time.time()
    while True:
        d = c.recv(65536)
        if not d:
            break
        total += len(d)
        win += len(d)
        now = time.time()
        if now - t0 >= 1.0:
            print(f"[nettest] {win/1024/1024:.2f} MB/s (누적 {total/1024/1024:.1f} MB)")
            win = 0
            t0 = now
    print(f"[nettest] 종료, 총 {total/1024/1024:.1f} MB 수신")


def run_client(host: str, port: int, seconds: float) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(f"[nettest] 접속 시도 {host}:{port}")
    s.connect((host, port))
    print("[nettest] 연결 성공, 송신 시작")
    buf = b"\xab" * 65536
    sent = 0
    t_end = time.time() + seconds
    while time.time() < t_end:
        s.sendall(buf)
        sent += len(buf)
    s.close()
    print(f"[nettest] 송신 완료: {sent/1024/1024:.1f} MB in {seconds:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="store_true")
    ap.add_argument("--client", metavar="PC_IP")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()
    if args.server:
        run_server(args.port)
    elif args.client:
        run_client(args.client, args.port, args.seconds)
    else:
        ap.error("--server 또는 --client <PC_IP> 중 하나를 지정하세요")


if __name__ == "__main__":
    main()
