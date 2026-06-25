"""PC <-> Raspberry Pi TCP 통신 프로토콜 (Pi 측 사본).

서버의 app/comms/protocol.py 와 동일한 프레이밍/패킷 정의를 Pi 에서 독립 실행할 수
있도록 복제한 것이다. 둘 중 하나를 바꾸면 반드시 양쪽을 함께 맞춰야 한다.

메시지 프레이밍: [1B type][4B length(big-endian)][payload]
패킷(little-endian):
    ControlPacket "<iii" (pan_target, tilt_target, trigger)  PC -> Pi
    StatusPacket  "<ii"  (pan_current, tilt_current)         Pi -> PC
    LiDAR         "<i"   (distance_mm)                        Pi -> PC
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum

_CONTROL_FMT = "<iii"
_STATUS_FMT = "<ii"
_LIDAR_FMT = "<i"

CONTROL_SIZE = struct.calcsize(_CONTROL_FMT)
STATUS_SIZE = struct.calcsize(_STATUS_FMT)
LIDAR_SIZE = struct.calcsize(_LIDAR_FMT)


@dataclass
class ControlPacket:
    pan_target: int
    tilt_target: int
    trigger: int = 0

    def pack(self) -> bytes:
        return struct.pack(_CONTROL_FMT, self.pan_target, self.tilt_target, self.trigger)

    @classmethod
    def unpack(cls, data: bytes) -> "ControlPacket":
        return cls(*struct.unpack(_CONTROL_FMT, data))


@dataclass
class StatusPacket:
    pan_current: int
    tilt_current: int

    def pack(self) -> bytes:
        return struct.pack(_STATUS_FMT, self.pan_current, self.tilt_current)

    @classmethod
    def unpack(cls, data: bytes) -> "StatusPacket":
        return cls(*struct.unpack(_STATUS_FMT, data))


def pack_lidar(distance_mm: int) -> bytes:
    return struct.pack(_LIDAR_FMT, distance_mm)


def unpack_lidar(data: bytes) -> int:
    return struct.unpack(_LIDAR_FMT, data)[0]


class MsgType(IntEnum):
    FRAME = 1
    STATUS = 2
    LIDAR = 3
    CONTROL = 4
    RANGE_REQ = 5   # PC -> Pi: 측거 요청(payload 없음). Pi 가 LiDAR 트리거 후 LIDAR 로 응답
    MOTOR = 6       # PC -> Pi: HAT 모터 제어(JSON). DC 모터/서보를 웹에서 조종


def pack_motor(cmd: dict) -> bytes:
    """HAT 모터 명령(dict)을 JSON 바이트로 인코딩. 예: {"kind":"dc","dir":"fwd","speed":120}."""
    return json.dumps(cmd).encode("utf-8")


def unpack_motor(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


_HEADER_FMT = ">BI"
HEADER_SIZE = struct.calcsize(_HEADER_FMT)


def encode_message(msg_type: MsgType, payload: bytes) -> bytes:
    return struct.pack(_HEADER_FMT, int(msg_type), len(payload)) + payload


def _recv_exactly(sock, n: int):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock):
    header = _recv_exactly(sock, HEADER_SIZE)
    if header is None:
        return None
    type_val, length = struct.unpack(_HEADER_FMT, header)
    payload = _recv_exactly(sock, length) if length else b""
    if payload is None:
        return None
    return MsgType(type_val), payload


def send_message(sock, msg_type: MsgType, payload: bytes) -> None:
    sock.sendall(encode_message(msg_type, payload))
