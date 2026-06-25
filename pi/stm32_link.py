"""STM32 와의 UART 링크 (Pi 측).

STM32 펌웨어(firmware/stm32_main.c)와 맞춘 경량 프로토콜:
    Pi  -> STM32 명령 (5바이트): [0xAA][pan][tilt][trigger][checksum]
                                   checksum = (pan+tilt+trigger) & 0xFF
    STM32 -> Pi 상태 (4바이트):   [0x55][pan_current][tilt_current][checksum]
                                   checksum = (pan_current+tilt_current) & 0xFF

각도는 0~180 으로 클램프되어 1바이트로 전송된다.
"""
from __future__ import annotations

import threading

import serial  # pyserial

CMD_HEADER = 0xAA
STATUS_HEADER = 0x55
STATUS_LEN = 4


def _clamp_byte(v: int, lo: int = 0, hi: int = 180) -> int:
    return max(lo, min(hi, int(v))) & 0xFF


class Stm32Link:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.1) -> None:
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self._tx_lock = threading.Lock()
        self._buf = bytearray()

    def send_command(self, pan: int, tilt: int, trigger: int) -> None:
        pan_b = _clamp_byte(pan)
        tilt_b = _clamp_byte(tilt)
        trig_b = 1 if trigger else 0
        chk = (pan_b + tilt_b + trig_b) & 0xFF
        frame = bytes([CMD_HEADER, pan_b, tilt_b, trig_b, chk])
        with self._tx_lock:
            self.ser.write(frame)

    def read_status(self):
        """수신 버퍼를 파싱해 최신 상태 (pan, tilt) 를 반환. 없으면 None.

        스트림에서 0x55 헤더로 동기화하여 4바이트 패킷을 추출하고 체크섬을 확인한다.
        """
        data = self.ser.read(64)
        if data:
            self._buf.extend(data)

        result = None
        # 버퍼에서 유효한 패킷을 가능한 만큼 소비(가장 최신 값 사용)
        while len(self._buf) >= STATUS_LEN:
            if self._buf[0] != STATUS_HEADER:
                self._buf.pop(0)
                continue
            pan, tilt, chk = self._buf[1], self._buf[2], self._buf[3]
            if chk == ((pan + tilt) & 0xFF):
                result = (pan, tilt)
                del self._buf[:STATUS_LEN]
            else:
                # 체크섬 불일치 -> 헤더 한 칸 밀어 재동기화
                self._buf.pop(0)
        return result

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
