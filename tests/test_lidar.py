"""TF-Luna 파서(pi/lidar.py) 검증 — 합성 9바이트 프레임.

프레임: [0x59][0x59][Dist_L][Dist_H][Amp_L][Amp_H][Temp_L][Temp_H][Checksum]
실행: .venv\\Scripts\\python -m tests.test_lidar
"""
import importlib.util
import sys
from pathlib import Path


class FakeSerial:
    def __init__(self, *a, **k):
        self._rx = bytearray()

    def feed(self, data):
        self._rx.extend(data)

    def read(self, n):
        chunk = bytes(self._rx[:n])
        del self._rx[:n]
        return chunk

    def close(self):
        pass


def _frame(dist, amp=200, temp=0):
    b = [0x59, 0x59, dist & 0xFF, (dist >> 8) & 0xFF,
         amp & 0xFF, (amp >> 8) & 0xFF, temp & 0xFF, (temp >> 8) & 0xFF]
    b.append(sum(b) & 0xFF)
    return bytes(b)


def _load():
    import serial
    serial.Serial = FakeSerial
    path = Path(__file__).resolve().parent.parent / "pi" / "lidar.py"
    spec = importlib.util.spec_from_file_location("pi_lidar", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pi_lidar"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def test_cm_to_mm():
    l = mod.TFLuna("FAKE")            # 기본 cm 포맷
    l.ser.feed(_frame(25))           # 25 cm
    assert l.read_distance_mm() == 250


def test_mm_mode():
    l = mod.TFLuna("FAKE", unit_mm=True)
    l.ser.feed(_frame(1875))         # 1875 mm
    assert l.read_distance_mm() == 1875


def test_low_amp_skipped():
    l = mod.TFLuna("FAKE")
    l.ser.feed(_frame(30, amp=50))   # Amp<100 -> 신뢰불가, 무시
    assert l.read_distance_mm() is None


def test_bad_checksum_resync():
    l = mod.TFLuna("FAKE")
    bad = bytearray(_frame(40)); bad[8] ^= 0xFF   # 체크섬 깨뜨림
    l.ser.feed(bytes(bad))
    l.ser.feed(_frame(33))                        # 다음 유효 프레임
    assert l.read_distance_mm() == 330


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("lidar: 모든 테스트 통과")


if __name__ == "__main__":
    _run_all()
