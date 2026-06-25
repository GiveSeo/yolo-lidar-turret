"""STM32 링크(pi/stm32_link.py)의 바이트 포맷 검증 — 페이크 시리얼 사용.

firmware/stm32_main.c 의 프로토콜과 일치하는지 확인:
  명령 5B: [0xAA][pan][tilt][trigger][checksum=(pan+tilt+trig)&0xFF]
  상태 4B: [0x55][pan][tilt][checksum=(pan+tilt)&0xFF]

실행: .venv\\Scripts\\python -m tests.test_stm32_link
"""
import importlib.util
import sys
from pathlib import Path


class FakeSerial:
    """pyserial.Serial 대체: write 버퍼 기록 + read 큐 제공."""
    def __init__(self, *a, **k):
        self.written = bytearray()
        self._rx = bytearray()

    def write(self, data):
        self.written.extend(data)

    def feed(self, data):  # STM32 -> Pi 방향 주입
        self._rx.extend(data)

    def read(self, n):
        chunk = bytes(self._rx[:n])
        del self._rx[:n]
        return chunk

    def close(self):
        pass


def _load_stm32_link():
    import serial
    serial.Serial = FakeSerial  # 실제 포트 열지 않도록 패치
    path = Path(__file__).resolve().parent.parent / "pi" / "stm32_link.py"
    spec = importlib.util.spec_from_file_location("pi_stm32_link", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pi_stm32_link"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_stm32_link()


def test_send_command_bytes():
    link = mod.Stm32Link("FAKE")
    link.send_command(95, 130, 1)
    w = link.ser.written
    assert w[0] == 0xAA
    assert w[1] == 95 and w[2] == 130 and w[3] == 1
    assert w[4] == ((95 + 130 + 1) & 0xFF)


def test_send_command_clamps_angle():
    link = mod.Stm32Link("FAKE")
    link.send_command(250, -10, 5)   # 클램프: 180, 0, trigger->1
    w = link.ser.written
    assert w[1] == 180 and w[2] == 0 and w[3] == 1


def test_read_status_parses_latest():
    link = mod.Stm32Link("FAKE")
    # 유효 상태 2개 연속 주입 -> 최신(102,77) 반환
    for pan, tilt in [(88, 90), (102, 77)]:
        link.ser.feed(bytes([0x55, pan, tilt, (pan + tilt) & 0xFF]))
    assert link.read_status() == (102, 77)


def test_read_status_resync_on_garbage():
    link = mod.Stm32Link("FAKE")
    link.ser.feed(b"\x00\x13\x55")                       # 앞쪽 쓰레기
    link.ser.feed(bytes([0x55, 100, 50, (100 + 50) & 0xFF]))
    assert link.read_status() == (100, 50)


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("stm32-link: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
