"""YOLOv8 객체 검출 래퍼.

커스텀 표적 가중치(models/yolo/best.pt)를 로드하여 프레임에서 객체를 검출한다.
가중치가 없으면 사전학습 모델(yolov8n.pt)로 폴백하여 개발/데모가 가능하게 한다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import config
from app.state import Detection

logger = logging.getLogger(__name__)


class Detector:
    def __init__(
        self,
        weights: Optional[Path] = None,
        device: Optional[str] = None,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        imgsz: Optional[int] = None,
    ) -> None:
        # ultralytics 는 무겁고 torch 를 끌어오므로 지연 임포트한다.
        from ultralytics import YOLO

        self.conf = conf if conf is not None else config.yolo.conf_threshold
        self.iou = iou if iou is not None else config.yolo.iou_threshold
        self.imgsz = imgsz if imgsz is not None else config.yolo.imgsz
        self.device = device if device is not None else config.yolo.device

        from app.config import YOLO_WEIGHTS

        chosen = Path(weights) if weights else YOLO_WEIGHTS
        if not Path(chosen).exists():
            logger.warning(
                "커스텀 가중치 %s 가 없어 사전학습 모델 %s 로 폴백합니다.",
                chosen, config.yolo.fallback_weights,
            )
            chosen = config.yolo.fallback_weights  # ultralytics 가 자동 다운로드

        logger.info("YOLO 모델 로드: %s (device=%s)", chosen, self.device)
        self.model = YOLO(str(chosen))
        self.names: dict[int, str] = self.model.names

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """BGR numpy 프레임에서 객체를 검출하여 Detection 리스트로 반환한다.

        ByteTrack 트래킹으로 같은 물체에 프레임 간 일관된 id 를 부여한다(persist).
        그래야 표적이 빠르게 움직여도 선택(빨간 박스)이 유지된다.
        트래킹이 불가하면 프레임 내 순번으로 폴백한다.
        """
        try:
            results = self.model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                device=self.device,
                conf=self.conf,
                iou=self.iou,
                imgsz=self.imgsz,
                verbose=False,
            )
        except Exception as e:  # noqa: BLE001 — 트래커 문제 시 일반 검출로 폴백
            logger.warning("트래킹 실패, predict 폴백: %s", e)
            results = self.model.predict(
                frame, device=self.device, conf=self.conf,
                iou=self.iou, imgsz=self.imgsz, verbose=False,
            )

        detections: list[Detection] = []
        if not results:
            return detections

        r = results[0]
        boxes = r.boxes
        if boxes is None:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        track_ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None

        for i in range(len(xyxy)):
            x1, y1, x2, y2 = xyxy[i].tolist()
            cls_id = int(clss[i])
            det_id = int(track_ids[i]) if track_ids is not None else i
            detections.append(
                Detection(
                    id=det_id,
                    cls=cls_id,
                    label=self.names.get(cls_id, str(cls_id)),
                    conf=float(confs[i]),
                    x1=x1, y1=y1, x2=x2, y2=y2,
                )
            )
        return detections


class DemoDetector:
    """오프라인 검증용 색상 블롭 검출기 (YOLO 불필요).

    합성 목 영상에 그려진 밝은 마젠타(BGR 근사 [255,0,255]) 표적을 HSV 임계값으로
    찾아 Detection 으로 반환한다. 조준 루프 수렴/통신/판정 흐름을 GPU·가중치 없이
    결정적으로 테스트할 수 있다. 실제 운용에는 YOLO Detector 를 쓴다.
    """

    def __init__(self, min_area: int = 150) -> None:
        self.min_area = min_area

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import cv2

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # 마젠타 계열 (Hue ~150) 범위
        mask = cv2.inRange(hsv, (140, 120, 120), (170, 255, 255))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[Detection] = []
        idx = 0
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w * h < self.min_area:
                continue
            detections.append(
                Detection(id=idx, cls=0, label="target", conf=0.99,
                          x1=float(x), y1=float(y), x2=float(x + w), y2=float(y + h))
            )
            idx += 1
        return detections


def get_detector():
    """환경변수 DETECTOR 로 검출기를 선택한다 ("yolo"(기본) | "demo")."""
    import os

    mode = os.environ.get("DETECTOR", "yolo").lower()
    if mode == "demo":
        logger.info("DemoDetector(색상 블롭) 사용")
        return DemoDetector()
    return Detector()
