"""전역 시스템 런타임 상태.

TCP 링크 스레드, FastAPI 비동기 핸들러, 조준 루프가 공유하는 상태를 보관한다.
모든 변경은 RLock 으로 보호한다. 스냅샷은 dict 로 떠서 WebSocket 으로 브라우저에 전송한다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Phase(str, Enum):
    IDLE = "idle"            # 대기 (표적 미선택)
    AIMING = "aiming"        # 자동 조준 중 (중앙 정렬)
    RANGING = "ranging"      # LiDAR 거리 측정
    FIRING = "firing"        # 발사 각도 이동 + 발사
    JUDGING = "judging"      # 발사 후 명중 신호 대기
    RESULT = "result"        # HIT / MISS 확정


@dataclass
class Detection:
    """YOLO 검출 1건. 좌표는 프레임 픽셀 기준."""
    id: int
    cls: int
    label: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)


@dataclass
class SystemState:
    # 연결 상태
    rpi_connected: bool = False
    esp32_seen: bool = False

    # 영상/검출
    frame_width: int = 0
    frame_height: int = 0
    detections: list[Detection] = field(default_factory=list)

    # 표적 선택 (detections 중 선택된 id, 없으면 None)
    selected_id: Optional[int] = None

    # 서보 현재 각도 (STM32 -> StatusPacket)
    pan_current: int = 90
    tilt_current: int = 90

    # 마지막 측정 거리(mm)
    distance_mm: Optional[int] = None

    # 진행 단계 / 결과
    phase: Phase = Phase.IDLE
    fire_time: Optional[float] = None
    result: Optional[str] = None   # "HIT" | "MISS" | None
    message: str = ""

    # 피드백 보정 (sim-to-real)
    tilt_bias: float = 0.0                       # 현재 누적 tilt 보정(도)
    pan_bias: float = 0.0                        # 현재 누적 pan(좌우) 보정(도)
    shots_count: int = 0                         # 누적 발사 기록 수
    last_shot: Optional[dict] = None             # 직전 발사 정보(피드백용)

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # --- 편의 메서드 -----------------------------------------------------
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def selected_detection(self) -> Optional[Detection]:
        with self._lock:
            if self.selected_id is None:
                return None
            for d in self.detections:
                if d.id == self.selected_id:
                    return d
            return None

    def snapshot(self) -> dict[str, Any]:
        """WebSocket 으로 보낼 JSON 직렬화용 스냅샷(락 보호)."""
        with self._lock:
            return {
                "rpi_connected": self.rpi_connected,
                "esp32_seen": self.esp32_seen,
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
                "detections": [asdict(d) for d in self.detections],
                "selected_id": self.selected_id,
                "pan_current": self.pan_current,
                "tilt_current": self.tilt_current,
                "distance_mm": self.distance_mm,
                "phase": self.phase.value,
                "result": self.result,
                "message": self.message,
                "tilt_bias": round(self.tilt_bias, 2),
                "pan_bias": round(self.pan_bias, 2),
                "shots_count": self.shots_count,
                "ts": time.time(),
            }


# 전역 단일 상태 인스턴스
state = SystemState()
