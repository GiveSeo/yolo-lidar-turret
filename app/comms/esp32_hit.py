"""ESP32 명중 신호 모니터.

ESP32 가 Wi-Fi 로 PC 의 HTTP 엔드포인트(POST /api/hit)를 호출하면 signal_hit()
이 불린다. 엔진은 발사 직후 wait_for_hit(timeout) 으로 정해진 시간 동안 명중
신호를 기다린다. 신호와 함께 온 payload(센서 피크값 등)와 도착 시각을 보관해,
판정 후 엔진이 발사 로그(DB)에 함께 기록할 수 있게 한다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class HitMonitor:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._armed = False
        self._lock = threading.Lock()
        self._last_payload: Optional[dict] = None  # 마지막 명중 신호 payload
        self._last_hit_ts: Optional[float] = None  # 마지막 명중 신호 도착(epoch)

    def arm(self) -> None:
        """발사 직전 호출. 이전 신호/payload 를 비우고 대기를 활성화한다."""
        with self._lock:
            self._event.clear()
            self._armed = True
            self._last_payload = None
            self._last_hit_ts = None

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def signal_hit(self, payload: Optional[dict] = None) -> bool:
        """ESP32 명중 신호 수신 시 호출. 무장 상태였으면 True.

        payload 는 ESP32 가 보낸 JSON (예: {"source":"esp32","ms":..,"value":..}).
        """
        with self._lock:
            armed = self._armed
            self._last_payload = payload or {}
            self._last_hit_ts = time.time()
        self._event.set()
        return armed

    def wait_for_hit(self, timeout: float) -> bool:
        """timeout 초 동안 명중 신호를 대기. 수신하면 True(HIT), 아니면 False(MISS)."""
        hit = self._event.wait(timeout)
        self.disarm()
        return hit

    def poll_hit(self, timeout: float) -> bool:
        """timeout 초 동안 명중 신호를 대기(무장 해제는 하지 않음).

        카운트다운처럼 짧은 간격으로 여러 번 나눠 기다릴 때 쓴다. 신호가 오면
        즉시 True. 대기를 다 마친 뒤에는 호출측에서 disarm() 을 한 번 호출한다.
        """
        return self._event.wait(timeout)

    def last_hit_info(self) -> dict[str, Any]:
        """직전 명중 신호의 payload/도착시각. 센서값(value/peak)·esp32 ms 를 정규화해 반환."""
        with self._lock:
            payload = dict(self._last_payload or {})
            ts = self._last_hit_ts
        # 센서 피크값: ESP32 가 보내면 value/peak/sensor 중 하나로 온다고 가정
        sensor_value = None
        for k in ("value", "peak", "sensor", "adc"):
            if isinstance(payload.get(k), (int, float)):
                sensor_value = float(payload[k])
                break
        esp32_ms = payload.get("ms") if isinstance(payload.get("ms"), (int, float)) else None
        return {"payload": payload, "ts": ts,
                "sensor_value": sensor_value, "esp32_ms": esp32_ms}
