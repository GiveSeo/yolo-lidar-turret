"""TF-Luna LiDAR 리더 (Pi 측, UART).

기본 출력 포맷 (9-byte/cm), 115200 8N1:
    [0x59][0x59][Dist_L][Dist_H][Amp_L][Amp_H][Temp_L][Temp_H][Checksum]
    Dist  = Dist_L + (Dist_H << 8)   단위: cm  (mm 포맷으로 바꾼 경우 unit_mm=True)
    Amp   = Amp_L + (Amp_H << 8)     신호강도. Amp<100 또는 ==65535 면 거리 신뢰 불가
    Checksum = 앞 8바이트 합의 하위 8비트

read_distance_mm() 은 스트림에서 최신 유효 프레임을 파싱해 거리(mm)를 반환한다.
"""
from __future__ import annotations

import serial  # pyserial

FRAME_HEAD = 0x59
FRAME_LEN = 9
AMP_MIN = 100


class TFLuna:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.05,
                 unit_mm: bool = False) -> None:
        """unit_mm=True 면 센서가 9-byte/mm 포맷으로 설정된 경우(거리값이 mm)."""
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.unit_mm = unit_mm
        self._buf = bytearray()
        self._last_mm = None

    def set_trigger_mode(self) -> None:
        """TF-Luna 를 트리거 모드(frame rate 0)로 전환 → 자동 측정 중지(IR off).

        명령(체크섬=앞바이트 합&0xFF):
          frame rate=0 : 5A 06 03 00 00 63   (트리거 모드 진입)
        이후 trigger_once() 를 보낼 때만 1회 측정·발광한다.
        """
        cmd = bytes([0x5A, 0x06, 0x03, 0x00, 0x00, 0x63])
        self.ser.write(cmd)
        self.ser.flush()
        self._buf.clear()

    def set_continuous_mode(self, hz: int = 100) -> None:
        """TF-Luna 를 연속 측정 모드(frame rate hz)로 → 자동 측정·IR 발광 ON.

        명령: 5A 06 03 [rate_L] [rate_H] [chk]   (chk=앞바이트 합&0xFF)
        """
        rate = max(1, min(250, int(hz)))
        lo, hi = rate & 0xFF, (rate >> 8) & 0xFF
        chk = (0x5A + 0x06 + 0x03 + lo + hi) & 0xFF
        self.ser.write(bytes([0x5A, 0x06, 0x03, lo, hi, chk]))
        self.ser.flush()
        self._buf.clear()

    def trigger_once(self) -> None:
        """1회 측정 트리거: 5A 04 04 62. 직후 read_distance_mm() 로 응답을 읽는다."""
        self.ser.write(bytes([0x5A, 0x04, 0x04, 0x62]))
        self.ser.flush()

    def measure_once_mm(self, settle: float = 0.06):
        """트리거 모드에서 1회 측정값(mm)을 얻는다(트리거 → 잠깐 대기 → 파싱)."""
        import time
        self._buf.clear()
        self.trigger_once()
        time.sleep(settle)
        return self.read_distance_mm()

    def read_distance_mm(self):
        """최신 유효 거리(mm)를 반환. 새 값이 없으면 마지막 값(또는 None)."""
        data = self.ser.read(64)
        if data:
            self._buf.extend(data)

        while len(self._buf) >= FRAME_LEN:
            if not (self._buf[0] == FRAME_HEAD and self._buf[1] == FRAME_HEAD):
                self._buf.pop(0)
                continue
            frame = self._buf[:FRAME_LEN]
            checksum = sum(frame[:8]) & 0xFF
            if checksum != frame[8]:
                self._buf.pop(0)   # 체크섬 불일치 -> 재동기화
                continue
            dist = frame[2] + (frame[3] << 8)
            amp = frame[4] + (frame[5] << 8)
            del self._buf[:FRAME_LEN]
            if amp < AMP_MIN or amp == 0xFFFF:
                continue           # 신뢰 불가 프레임은 건너뜀
            self._last_mm = dist if self.unit_mm else dist * 10  # cm -> mm
        return self._last_mm

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
