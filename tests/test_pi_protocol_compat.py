"""Pi 측 protocol(pi/protocol.py)과 서버 protocol(app/comms/protocol.py)의
바이트 호환성 교차 검증.

실행: .venv\\Scripts\\python -m tests.test_pi_protocol_compat
"""
import importlib.util
import sys
from pathlib import Path

from app.comms import protocol as srv

# pi/protocol.py 를 별도 모듈로 로드(패키지 아님)
_pi_path = Path(__file__).resolve().parent.parent / "pi" / "protocol.py"
_spec = importlib.util.spec_from_file_location("pi_protocol", _pi_path)
pi = importlib.util.module_from_spec(_spec)
sys.modules["pi_protocol"] = pi   # dataclass + future annotations 해석에 필요
_spec.loader.exec_module(pi)


def test_control_cross():
    # 서버가 보낸 CONTROL 을 Pi 가 동일하게 해석
    data = srv.ControlPacket(95, 130, 1).pack()
    p = pi.ControlPacket.unpack(data)
    assert (p.pan_target, p.tilt_target, p.trigger) == (95, 130, 1)


def test_status_cross():
    # Pi 가 보낸 STATUS 를 서버가 동일하게 해석
    data = pi.StatusPacket(88, 102).pack()
    s = srv.StatusPacket.unpack(data)
    assert (s.pan_current, s.tilt_current) == (88, 102)


def test_lidar_cross():
    assert srv.unpack_lidar(pi.pack_lidar(2510)) == 2510
    assert pi.unpack_lidar(srv.pack_lidar(180)) == 180


def test_framing_cross():
    # Pi 가 인코딩한 메시지를 서버가 디코딩(소켓 페어)
    import socket
    a, b = socket.socketpair()
    try:
        a.sendall(pi.encode_message(pi.MsgType.LIDAR, pi.pack_lidar(1234)))
        t, payload = srv.recv_message(b)
        assert t == srv.MsgType.LIDAR and srv.unpack_lidar(payload) == 1234
    finally:
        a.close(); b.close()


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("pi-protocol 호환: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
