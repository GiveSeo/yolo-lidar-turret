"""프로토콜 단위 테스트: 패킷 pack/unpack 라운드트립 + 메시지 프레이밍.

실행: .venv\\Scripts\\python -m tests.test_protocol
"""
import io
import socket
import threading

from app.comms.protocol import (
    CONTROL_SIZE,
    STATUS_SIZE,
    ControlPacket,
    MsgType,
    StatusPacket,
    encode_message,
    pack_lidar,
    recv_message,
    send_message,
    unpack_lidar,
)


def test_control_roundtrip():
    p = ControlPacket(pan_target=123, tilt_target=-45, trigger=1)
    data = p.pack()
    assert len(data) == CONTROL_SIZE == 12
    assert ControlPacket.unpack(data) == p


def test_status_roundtrip():
    p = StatusPacket(pan_current=90, tilt_current=100)
    data = p.pack()
    assert len(data) == STATUS_SIZE == 8
    assert StatusPacket.unpack(data) == p


def test_lidar_roundtrip():
    assert unpack_lidar(pack_lidar(1875)) == 1875


def test_message_framing_over_socket():
    """실제 소켓 페어로 프레이밍/부분수신을 검증."""
    a, b = socket.socketpair()
    try:
        # 송신측: 여러 메시지 연속 전송
        send_message(a, MsgType.CONTROL, ControlPacket(10, 20, 0).pack())
        send_message(a, MsgType.LIDAR, pack_lidar(500))
        send_message(a, MsgType.FRAME, b"\xff\xd8jpegbytes\xff\xd9")

        t1, p1 = recv_message(b)
        assert t1 == MsgType.CONTROL and ControlPacket.unpack(p1) == ControlPacket(10, 20, 0)
        t2, p2 = recv_message(b)
        assert t2 == MsgType.LIDAR and unpack_lidar(p2) == 500
        t3, p3 = recv_message(b)
        assert t3 == MsgType.FRAME and p3 == b"\xff\xd8jpegbytes\xff\xd9"
    finally:
        a.close()
        b.close()


def test_recv_returns_none_on_close():
    a, b = socket.socketpair()
    a.close()
    assert recv_message(b) is None
    b.close()


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("protocol: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
