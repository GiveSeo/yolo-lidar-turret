"""Raspberry Pi 와의 TCP 링크 (전송 계층).

PC 가 TCP 서버로 listen 하고 RPi 가 접속한다. 수신한 메시지를 종류별로 처리한다:
  - FRAME  : JPEG 디코드 -> YOLO 검출 -> 상태 갱신 -> 주석 프레임을 hub 에 저장
  - STATUS : 현재 서보 각도 갱신
  - LIDAR  : 최신 거리 갱신
그리고 send_control() 로 ControlPacket 을 RPi 로 보낸다.

블로킹 소켓을 별도 스레드에서 처리하며, FastAPI(비동기)와는 스레드세이프한
최신 프레임 홀더(latest_jpeg)와 전역 state 를 통해 통신한다.
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Optional

import cv2
import numpy as np

from app.comms.protocol import (
    ControlPacket,
    MsgType,
    StatusPacket,
    pack_motor,
    recv_message,
    send_message,
    unpack_lidar,
)
from app.config import config
from app.state import state

logger = logging.getLogger(__name__)


class RpiLink:
    def __init__(self, detector=None) -> None:
        self.detector = detector
        self._srv_sock: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._client_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 최신 주석(annotated) 프레임 JPEG 홀더 (video_feed 가 읽음)
        self._frame_lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None

        # 수신된 원본 프레임 슬롯 (수신 스레드 -> 검출 워커). 최신만 유지(중간 프레임 드롭).
        self._raw_lock = threading.Lock()
        self._latest_raw = None          # np.ndarray (BGR)
        self._raw_seq = 0                # 새 프레임 수신 카운터
        self._new_frame = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._frame_count = 0

        # 선택 유지 유예: 선택 표적이 N프레임 연속 미검출일 때만 해제(이동 중 블러 드롭아웃 견디기)
        self._sel_miss = 0
        self._SEL_GRACE_FRAMES = 60       # ↑(20→60): 조준 이동 중 검출이 끊겨도 더 오래 락 유지
        # 재바인딩: 트래커 id 가 끊겨도 마지막 위치에서 가장 가까운 검출로 선택 이어줌
        # (서보 회전으로 카메라가 움직이면 id 가 바뀌므로 위치 기반으로 락 유지)
        self._last_sel_center = None      # (cx, cy) 픽셀
        self._REBIND_FRAC = 0.5           # ↑(0.35→0.5): 이동으로 표적이 더 튀어도 같은 표적으로 간주
        # 자동 재선택: 락이 풀린 뒤에도 잠시 마지막 위치를 기억해, 표적이 같은 자리에
        # 다시 나타나면 자동으로 재선택한다(잠깐 놓쳐도 조준이 복구되도록).
        self._tracked_id = None           # 현재 우리가 잠근 id(외부 클릭으로 바뀌면 기준 리셋용)
        self._reacquire_left = 0          # 남은 자동 재획득 시도 프레임 수
        self._REACQUIRE_FRAMES = 60       # 락 해제 후 이 프레임 동안 재획득 시도

    # --- 수명주기 --------------------------------------------------------
    def start(self) -> None:
        self._running = True
        # 새 코드(수신/검출 분리) 로드 확인용 표식
        logger.info("RpiLink 시작 [v2: 수신/검출 분리 - 워커에서 YOLO]")
        self._thread = threading.Thread(target=self._serve, name="rpi-link", daemon=True)
        self._thread.start()
        # YOLO 검출은 별도 워커에서(수신 스레드를 막지 않도록)
        self._worker = threading.Thread(target=self._detector_loop, name="detector", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        self._new_frame.set()  # 워커 깨워서 종료
        with self._client_lock:
            if self._client:
                try:
                    self._client.close()
                except OSError:
                    pass
        if self._srv_sock:
            try:
                self._srv_sock.close()
            except OSError:
                pass

    # --- 외부 인터페이스 -------------------------------------------------
    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_jpeg

    def get_raw_jpeg(self, quality: int = 90) -> Optional[bytes]:
        """주석(박스/십자선) 없는 '원본' 최신 프레임을 JPEG 로 인코딩해 반환.

        학습용 데이터셋 수집(tools/collect_dataset.py)에서 쓴다. video_feed 는
        검출 박스가 그려진 프레임이라 라벨링용으로 부적합하므로 원본을 따로 제공한다.
        """
        with self._raw_lock:
            frame = self._latest_raw
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def send_control(self, pan_target: int, tilt_target: int, trigger: int = 0) -> bool:
        """ControlPacket 을 RPi 로 전송한다. 연결이 없으면 False."""
        pkt = ControlPacket(pan_target, tilt_target, trigger)
        with self._client_lock:
            if not self._client:
                return False
            try:
                send_message(self._client, MsgType.CONTROL, pkt.pack())
                return True
            except OSError as e:
                logger.warning("CONTROL 전송 실패: %s", e)
                return False

    def send_motor(self, cmd: dict) -> bool:
        """HAT 모터 명령(JSON)을 RPi 로 전송한다. 연결이 없으면 False.

        cmd 예: {"kind":"dc","dir":"fwd"|"back"|"stop","speed":0~255}
                {"kind":"servo","val":200~500}
        """
        with self._client_lock:
            if not self._client:
                return False
            try:
                send_message(self._client, MsgType.MOTOR, pack_motor(cmd))
                return True
            except OSError as e:
                logger.warning("MOTOR 전송 실패: %s", e)
                return False

    def send_range_request(self) -> bool:
        """측거 요청(RANGE_REQ)을 RPi 로 전송한다. Pi 가 LiDAR 를 1회 트리거해 LIDAR 로 응답."""
        with self._client_lock:
            if not self._client:
                return False
            try:
                send_message(self._client, MsgType.RANGE_REQ, b"")
                return True
            except OSError as e:
                logger.warning("RANGE_REQ 전송 실패: %s", e)
                return False

    # --- 내부 루프 -------------------------------------------------------
    def _serve(self) -> None:
        host, port = config.network.rpi_tcp_host, config.network.rpi_tcp_port
        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv_sock.bind((host, port))
        self._srv_sock.listen(5)
        logger.info("RPi TCP 서버 listen: %s:%d", host, port)

        # accept 전용 루프: 새 연결을 즉시 받고, 이전 연결은 바로 닫는다(최신 연결 우선).
        # 각 연결은 별도 스레드에서 처리해 accept 가 절대 막히지 않게 한다.
        while self._running:
            try:
                self._srv_sock.settimeout(1.0)
                try:
                    client, addr = self._srv_sock.accept()
                except socket.timeout:
                    continue
            except OSError:
                break

            logger.info("RPi 접속: %s", addr)
            try:
                client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass
            client.settimeout(8.0)  # 8초간 데이터 없으면 죽은 연결로 보고 정리

            # 이전 연결이 있으면 즉시 닫아 그 핸들러를 종료시킨다(죽은 연결 잔류 방지)
            with self._client_lock:
                old = self._client
                self._client = client
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            with state.lock:
                state.rpi_connected = True

            threading.Thread(target=self._client_session, args=(client, addr),
                             name="rpi-client", daemon=True).start()

    def _client_session(self, client: socket.socket, addr) -> None:
        try:
            self._handle_client(client)
        finally:
            with self._client_lock:
                # 더 새로운 연결로 교체된 경우 상태를 건드리지 않는다
                if self._client is client:
                    self._client = None
                    with state.lock:
                        state.rpi_connected = False
            try:
                client.close()
            except OSError:
                pass
            logger.info("RPi 연결 종료: %s", addr)

    def _handle_client(self, client: socket.socket) -> None:
        while self._running:
            try:
                msg = recv_message(client)
            except socket.timeout:
                # 8초간 수신 없음 = 죽거나 멈춘 연결 -> 정리하고 새 연결 대기
                logger.warning("수신 타임아웃 -> 연결 정리(죽은 연결 추정)")
                break
            except OSError as e:
                logger.warning("수신 오류 -> 연결 정리: %s", e)
                break
            if msg is None:
                break
            msg_type, payload = msg
            if msg_type == MsgType.FRAME:
                self._on_frame(payload)
            elif msg_type == MsgType.STATUS:
                self._on_status(payload)
            elif msg_type == MsgType.LIDAR:
                self._on_lidar(payload)

    def _on_frame(self, jpeg_bytes: bytes) -> None:
        """수신 스레드: 디코드만 하고 최신 프레임 슬롯에 저장(YOLO 는 워커가).

        무거운 추론을 여기서 하면 소켓을 못 비워 Pi 가 TCP 백프레셔로 멈춘다.
        그래서 여기서는 빠르게 디코드+저장만 하고 즉시 다음 수신으로 돌아간다.
        """
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("FRAME 디코드 실패 (%d bytes)", len(jpeg_bytes))
            return

        self._frame_count += 1
        if self._frame_count <= 5 or self._frame_count % 60 == 0:
            h, w = frame.shape[:2]
            logger.info("FRAME 수신 %d장 (%dx%d, %d bytes)",
                        self._frame_count, w, h, len(jpeg_bytes))

        with self._raw_lock:
            self._latest_raw = frame
            self._raw_seq += 1
        self._new_frame.set()

        # 검출 워커가 첫 결과를 내기 전(워밍업)에는 원본이라도 즉시 송출(검은화면 방지)
        with self._frame_lock:
            have = self._latest_jpeg is not None
        if not have:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self._frame_lock:
                    if self._latest_jpeg is None:
                        self._latest_jpeg = buf.tobytes()

    def _nearest_within(self, detections, ref, diag):
        """ref(픽셀)에 가장 가까운 검출이 재바인딩 임계(_REBIND_FRAC) 안이면 그 검출, 아니면 None.

        선택 유지(재바인딩)와 자동 재선택이 같은 위치-근접 판정을 공유한다.
        """
        lx, ly = ref
        near = min(detections, key=lambda d: (d.center[0] - lx) ** 2 + (d.center[1] - ly) ** 2)
        cx, cy = near.center
        if ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5 <= diag * self._REBIND_FRAC:
            return near
        return None

    def _detector_loop(self) -> None:
        """검출 워커: 최신 원본 프레임에 YOLO 검출+주석을 적용해 latest_jpeg 생성.

        수신 속도를 못 따라가면 중간 프레임을 드롭한다(항상 최신만 처리).
        """
        last_seq = -1
        while self._running:
            # 새 프레임이 올 때까지 대기(busy-wait 방지)
            if not self._new_frame.wait(timeout=1.0):
                continue
            self._new_frame.clear()
            with self._raw_lock:
                if self._raw_seq == last_seq or self._latest_raw is None:
                    continue
                frame = self._latest_raw
                last_seq = self._raw_seq

            h, w = frame.shape[:2]
            try:
                detections = self.detector.detect(frame) if self.detector else []
            except Exception as e:  # noqa: BLE001
                logger.exception("검출 오류: %s", e)
                detections = []

            with state.lock:
                state.frame_width = w
                state.frame_height = h
                state.detections = detections
                diag = (w * w + h * h) ** 0.5

                sel = state.selected_id
                # 외부(웹 클릭)에서 표적이 바뀌면 위치 기준/재획득 상태를 리셋
                if sel is not None and sel != self._tracked_id:
                    self._last_sel_center = None
                    self._sel_miss = 0
                    self._reacquire_left = 0

                if sel is not None:
                    # 선택 유지: 트래커 id 가 흔들려도(#35↔#37) 매 프레임 '마지막 선택 위치에
                    # 가장 가까운 검출'로 selected_id 를 갱신해 위치로 락을 유지한다.
                    cand = None
                    if detections:
                        if self._last_sel_center is None:
                            # 아직 위치 기준이 없으면 현재 id 매칭으로 기준 확보
                            cand = next((d for d in detections if d.id == sel), None)
                        else:
                            cand = self._nearest_within(detections, self._last_sel_center, diag)
                    if cand is not None:
                        state.selected_id = cand.id
                        self._last_sel_center = cand.center
                        self._sel_miss = 0
                    else:
                        self._sel_miss += 1
                        if self._sel_miss > self._SEL_GRACE_FRAMES:
                            # 락 해제 — 위치 기준이 있으면 잠시 자동 재획득을 시도한다.
                            state.selected_id = None
                            self._sel_miss = 0
                            self._reacquire_left = (
                                self._REACQUIRE_FRAMES if self._last_sel_center is not None else 0)
                elif self._last_sel_center is not None and self._reacquire_left > 0:
                    # 자동 재선택: 직전 표적 위치 근처에 검출이 다시 나타나면 재획득한다.
                    self._reacquire_left -= 1
                    cand = (self._nearest_within(detections, self._last_sel_center, diag)
                            if detections else None)
                    if cand is not None:
                        state.selected_id = cand.id
                        self._last_sel_center = cand.center
                        self._sel_miss = 0
                        self._reacquire_left = 0
                    elif self._reacquire_left == 0:
                        self._last_sel_center = None   # 재획득 창 만료 -> 기준 제거

                self._tracked_id = state.selected_id
                selected_id = state.selected_id

            annotated = self._annotate(frame, detections, selected_id)
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self._frame_lock:
                    self._latest_jpeg = buf.tobytes()

    @staticmethod
    def _annotate(frame, detections, selected_id):
        out = frame.copy()
        h, w = out.shape[:2]
        # 화면 중앙 십자선
        cv2.drawMarker(out, (w // 2, h // 2), (0, 255, 255),
                       markerType=cv2.MARKER_CROSS, markerSize=20, thickness=1)
        for d in detections:
            selected = d.id == selected_id
            color = (0, 0, 255) if selected else (0, 255, 0)
            thick = 3 if selected else 2
            cv2.rectangle(out, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), color, thick)
            cv2.putText(out, f"#{d.id} {d.label} {d.conf:.2f}",
                        (int(d.x1), int(d.y1) - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 1, cv2.LINE_AA)
        return out

    def _on_status(self, payload: bytes) -> None:
        st = StatusPacket.unpack(payload)
        with state.lock:
            state.pan_current = st.pan_current
            state.tilt_current = st.tilt_current

    def _on_lidar(self, payload: bytes) -> None:
        dist = unpack_lidar(payload)
        with state.lock:
            state.distance_mm = dist
