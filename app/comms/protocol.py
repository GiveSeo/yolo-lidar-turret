"""RPi <-> PC 통신 프로토콜.

개요의 구조체와 호환되는 고정 크기 패킷 + 단순 메시지 프레이밍을 정의한다.

패킷 (little-endian):
    ControlPacket { int32 pan_target; int32 tilt_target; int32 trigger; }  -> "<iii" (12B)  PC -> RPi -> STM32
    StatusPacket  { int32 pan_current; int32 tilt_current; }               -> "<ii"  (8B)   STM32 -> RPi -> PC

메시지 프레이밍 (TCP 스트림 위에서 메시지 경계를 구분):
    [1B type][4B length(big-endian uint32)][payload ...]

payload 종류 (MsgType):
    FRAME   : JPEG 로 인코딩된 카메라 프레임 바이트          (RPi -> PC)
    STATUS  : StatusPacket                                   (RPi -> PC)
    LIDAR   : int32 거리(mm)  -> "<i"                        (RPi -> PC)
    CONTROL : ControlPacket                                  (PC -> RPi)
    MOTOR   : JSON {kind:"dc"|"servo", ...}                  (PC -> RPi, HAT 모터)
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum

# --- 패킷 정의 ----------------------------------------------------------

_CONTROL_FMT = "<iii"
_STATUS_FMT = "<ii"
_LIDAR_FMT = "<i"

CONTROL_SIZE = struct.calcsize(_CONTROL_FMT)  # 12
STATUS_SIZE = struct.calcsize(_STATUS_FMT)    # 8
LIDAR_SIZE = struct.calcsize(_LIDAR_FMT)      # 4


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


# --- 메시지 프레이밍 ----------------------------------------------------

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


_HEADER_FMT = ">BI"  # 1B type + 4B length
HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 5


def encode_message(msg_type: MsgType, payload: bytes) -> bytes:
    """타입과 페이로드를 [1B type][4B len][payload] 프레임으로 인코딩한다."""
    return struct.pack(_HEADER_FMT, int(msg_type), len(payload)) + payload


def _recv_exactly(sock, n: int) -> bytes | None:
    """소켓에서 정확히 n 바이트를 읽는다. 연결이 끊기면 None 반환."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock) -> tuple[MsgType, bytes] | None:
    """소켓에서 한 메시지를 읽어 (MsgType, payload) 로 반환. 연결 종료 시 None."""
    header = _recv_exactly(sock, HEADER_SIZE)
    if header is None:
        return None
    type_val, length = struct.unpack(_HEADER_FMT, header)
    payload = _recv_exactly(sock, length) if length else b""
    if payload is None:
        return None
    return MsgType(type_val), payload


def send_message(sock, msg_type: MsgType, payload: bytes) -> None:
    """소켓으로 한 메시지를 프레이밍하여 전송한다."""
    sock.sendall(encode_message(msg_type, payload))
