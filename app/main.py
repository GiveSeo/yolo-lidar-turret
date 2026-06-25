"""FastAPI 진입점.

REST + WebSocket + MJPEG 영상 스트림을 제공하고, 시작 시 detector / RPi 링크 /
조준 엔진 / 명중 모니터를 초기화한다.

실행:
    .venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
또는:
    .venv\\Scripts\\python -m app.main
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.comms.esp32_hit import HitMonitor
from app.comms.rpi_link import RpiLink
from app.config import WEB_DIR, config
from app.engine import AimingEngine
from app.state import Phase, state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.main")

# 런타임 구성요소 (startup 에서 초기화)
ctx: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # detector 는 무거우므로 로드 실패해도 서버는 뜨게 한다(목/UI 테스트 가능).
    detector = None
    try:
        from app.vision.detector import get_detector
        detector = get_detector()
    except Exception as e:
        logger.warning("Detector 로드 실패(검출 없이 진행): %s", e)

    predictor = None
    try:
        from app.aiming.angle_model import load_predictor
        predictor = load_predictor()
        if predictor is None:
            logger.info("각도 MLP 체크포인트 없음 -> 폴백(현재 각도 사용).")
    except Exception as e:
        logger.warning("AnglePredictor 로드 실패: %s", e)

    # 피드백 보정 저장소 + 시작 시 calibration/shots 로드
    from app.feedback import FeedbackStore
    feedback = FeedbackStore(predictor=predictor)
    feedback.load()

    # 발사+명중판정 로그 DB(SQLite) 준비
    from app import db
    db.init_db()

    hit_monitor = HitMonitor()
    link = RpiLink(detector=detector)
    engine = AimingEngine(link=link, hit_monitor=hit_monitor, predictor=predictor)
    link.start()

    ctx.update(detector=detector, predictor=predictor, hit_monitor=hit_monitor,
               link=link, engine=engine, feedback=feedback)
    logger.info("서버 초기화 완료.")
    try:
        yield
    finally:
        link.stop()


app = FastAPI(title="AI 자동 조준 서버", lifespan=lifespan)


# --- 페이지 / 정적 파일 -------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/dashboard")
async def dashboard():
    """발사 로그 시각화 + AI 분석 대시보드."""
    return FileResponse(WEB_DIR / "dashboard.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# --- 영상 스트림 (MJPEG) ------------------------------------------------
@app.get("/video_feed")
async def video_feed():
    boundary = "frame"

    async def gen():
        last = None
        while True:
            jpeg = ctx["link"].get_latest_jpeg() if ctx.get("link") else None
            if jpeg is not None and jpeg is not last:
                last = jpeg
                yield (b"--" + boundary.encode() + b"\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
            await asyncio.sleep(0.04)  # ~25fps 상한

    return StreamingResponse(
        gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}"
    )


# --- 원본 스냅샷 (학습 데이터 수집용) ----------------------------------
@app.get("/api/snapshot")
async def snapshot():
    """주석 없는 최신 원본 프레임 1장(JPEG). tools/collect_dataset.py 가 폴링해 저장."""
    link = ctx.get("link")
    jpeg = link.get_raw_jpeg() if link else None
    if jpeg is None:
        return JSONResponse({"ok": False, "reason": "프레임 없음(카메라/Pi 미연결)"},
                            status_code=503)
    return Response(content=jpeg, media_type="image/jpeg")


# --- 상태 WebSocket -----------------------------------------------------
@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(state.snapshot())
            await asyncio.sleep(0.1)  # 10Hz
    except WebSocketDisconnect:
        pass


# --- REST API -----------------------------------------------------------
@app.get("/api/state")
async def get_state():
    return state.snapshot()


@app.post("/api/select/{det_id}")
async def select_target(det_id: int):
    """검출 id 로 표적을 선택한다."""
    with state.lock:
        exists = any(d.id == det_id for d in state.detections)
        if exists:
            state.selected_id = det_id
    return {"ok": exists, "selected_id": det_id if exists else None}


@app.post("/api/select_at")
async def select_at(payload: dict):
    """클릭/탭한 (정규화 또는 픽셀) 좌표로 표적을 선택한다.

    body: {"x": float, "y": float, "normalized": bool}
    """
    x = float(payload.get("x", 0))
    y = float(payload.get("y", 0))
    normalized = bool(payload.get("normalized", True))
    with state.lock:
        fw, fh = state.frame_width, state.frame_height
        if normalized:
            x, y = x * fw, y * fh
        chosen = None
        for d in state.detections:
            if d.x1 <= x <= d.x2 and d.y1 <= y <= d.y2:
                # 여러 박스가 겹치면 더 작은(가까운) 것을 우선
                area = (d.x2 - d.x1) * (d.y2 - d.y1)
                if chosen is None or area < chosen[1]:
                    chosen = (d.id, area)
        if chosen:
            state.selected_id = chosen[0]
    return {"ok": chosen is not None, "selected_id": chosen[0] if chosen else None}


@app.post("/api/clear_selection")
async def clear_selection():
    with state.lock:
        state.selected_id = None
    return {"ok": True}


@app.post("/api/clear_result")
async def clear_result():
    """직전 발사 결과(HIT/MISS) 배너를 닫는다. result 를 비우고 단계를 IDLE 로."""
    with state.lock:
        state.result = None
        if state.phase == Phase.RESULT:
            state.phase = Phase.IDLE
        state.message = ""
    return {"ok": True}


@app.post("/api/engage")
async def engage(payload: dict | None = None):
    """발사 시퀀스(조준->측거->발사->판정)를 시작한다.

    body: {"aim_only": true} 면 조준(트래킹)만 하고 발사하지 않는다(안전 테스트).
    """
    aim_only = bool((payload or {}).get("aim_only", False))
    engine: AimingEngine = ctx["engine"]
    started = engine.engage(aim_only=aim_only)
    return JSONResponse(
        {"ok": started, "aim_only": aim_only,
         "reason": None if started else "이미 진행 중이거나 표적 미선택"},
        status_code=200 if started else 409,
    )


@app.post("/api/hit")
async def esp32_hit(payload: dict | None = None):
    """ESP32 명중 신호 수신 엔드포인트 (Wi-Fi).

    payload(예: {"source":"esp32","ms":..,"value":..})는 명중 모니터에 보관됐다가
    판정 시 발사 로그(DB)에 센서값으로 함께 기록된다.
    """
    hm: HitMonitor = ctx["hit_monitor"]
    armed = hm.signal_hit(payload)
    with state.lock:
        state.esp32_seen = True
    logger.info("명중 신호 수신 (armed=%s) payload=%s", armed, payload)
    return {"ok": True, "armed": armed, "ts": time.time()}


@app.post("/api/home")
async def home_servos():
    """대포 서보(Pan/Tilt/Trigger)를 초기 위치로 복귀.

    pan=중립(pan_home_deg), tilt=수평(tilt_home_deg), trigger=0(걸쇠 유지).
    펌웨어 시작 자세(pan 90, tilt 0)와 동일하게 맞춘다.
    발사 시퀀스 진행 중에는 충돌 방지를 위해 거부한다(409).
    """
    engine: AimingEngine = ctx["engine"]
    if engine.is_running():
        return JSONResponse({"ok": False, "reason": "발사 시퀀스 진행 중 — 끝난 뒤 시도하세요"},
                            status_code=409)
    pan, tilt = config.fire.pan_home_deg, config.fire.tilt_home_deg
    ok = ctx["link"].send_control(pan, tilt, trigger=0)
    logger.info("대포 서보 초기화: pan=%d tilt=%d trigger=0 (ok=%s)", pan, tilt, ok)
    return JSONResponse({"ok": ok, "pan": pan, "tilt": tilt,
                         "reason": None if ok else "Pi 미연결"},
                        status_code=200 if ok else 409)


@app.post("/api/test_fire")
async def test_fire(payload: dict | None = None):
    """캘리브레이션용 '생' 발사: 지정 tilt(+pan)로 이동→정착→발사. 측거/MLP/판정 없음.

    body: {"tilt": int, "pan": int?(기본 정면), "settle": float?(기본 0.6s)}
    각도별로 쏘고 줄자로 낙하거리를 재서 해석식 탄도 파라미터를 보정할 때 쓴다.
    발사 시퀀스 진행 중에는 거부(409).
    """
    engine: AimingEngine = ctx["engine"]
    if engine.is_running():
        return JSONResponse({"ok": False, "reason": "발사 시퀀스 진행 중"}, status_code=409)
    p = payload or {}
    tilt = max(config.aiming.tilt_min_deg, min(config.aiming.tilt_max_deg, int(p.get("tilt", 0))))
    pan = max(config.aiming.pan_min_deg, min(config.aiming.pan_max_deg,
                                             int(p.get("pan", config.fire.pan_home_deg))))
    settle = max(0.1, min(3.0, float(p.get("settle", 0.6))))
    link = ctx["link"]
    if not link.send_control(pan, tilt, trigger=0):     # 이동(트리거 0)
        return JSONResponse({"ok": False, "reason": "Pi 미연결"}, status_code=409)
    await asyncio.sleep(settle)                          # 서보 정착 대기
    ok = link.send_control(pan, tilt, trigger=config.fire.trigger_release_value)  # 발사
    logger.info("[test_fire] pan=%d tilt=%d 발사(ok=%s)", pan, tilt, ok)
    return JSONResponse({"ok": ok, "pan": pan, "tilt": tilt},
                        status_code=200 if ok else 409)


@app.post("/api/motor/dc")
async def motor_dc(payload: dict):
    """HAT DC 모터 제어. body: {"dir":"fwd"|"back"|"stop", "speed":0~255}"""
    direction = str((payload or {}).get("dir", "stop"))
    speed = max(0, min(255, int((payload or {}).get("speed", 100))))
    ok = ctx["link"].send_motor({"kind": "dc", "dir": direction, "speed": speed})
    return JSONResponse({"ok": ok, "dir": direction, "speed": speed,
                         "reason": None if ok else "Pi 미연결"},
                        status_code=200 if ok else 409)


@app.post("/api/motor/servo")
async def motor_servo(payload: dict):
    """HAT 서보 제어. body: {"val":200~500}"""
    val = max(150, min(600, int((payload or {}).get("val", 350))))
    ok = ctx["link"].send_motor({"kind": "servo", "val": val})
    return JSONResponse({"ok": ok, "val": val,
                         "reason": None if ok else "Pi 미연결"},
                        status_code=200 if ok else 409)


@app.post("/api/feedback")
async def feedback(payload: dict):
    """운영자 착탄 피드백으로 발사각(tilt) + 좌우(pan) 보정.

    body: {"result":"hit"|"miss",
           "drop":"short"|"long"|"none", "error_cm":float,   # 앞뒤(tilt)
           "side":"left"|"right"|"none", "side_cm":float}     # 좌우(pan)
      drop=short(앞,짧음)->error 음수, long(뒤,김)->양수, none->0
      side=left(왼)->음수, right(오른)->양수, none->0
    """
    result = str(payload.get("result", "miss"))
    drop = str(payload.get("drop", "none"))
    error_cm = abs(float(payload.get("error_cm", 0.0)))
    error_m = {"short": -1.0, "long": 1.0, "none": 0.0}.get(drop, 0.0) * error_cm / 100.0

    side = str(payload.get("side", "none"))
    side_cm = abs(float(payload.get("side_cm", 0.0)))
    side_m = {"left": -1.0, "right": 1.0, "none": 0.0}.get(side, 0.0) * side_cm / 100.0

    store = ctx["feedback"]
    res = store.apply_feedback(result=result, error_m=error_m, ts=time.time(), side_m=side_m)
    return JSONResponse(res, status_code=200 if res.get("ok") else 409)


@app.post("/api/feedback/reset")
async def feedback_reset():
    """누적 보정(tilt_bias / pan_bias)을 0 으로 초기화."""
    ctx["feedback"].reset_bias()
    logger.info("보정 초기화 요청 -> tilt_bias=0, pan_bias=0")
    return {"ok": True, "tilt_bias": 0.0, "pan_bias": 0.0}


# --- 발사 로그 / 시각화 / AI 분석 ---------------------------------------
@app.get("/api/shots")
async def api_shots(limit: int = 200):
    """최근 발사 기록(최신순)."""
    from app import db
    return {"shots": db.fetch_shots(limit=max(1, min(limit, 2000)))}


@app.get("/api/stats")
async def api_stats():
    """명중률·거리구간별·타임라인 집계."""
    from app import db
    return db.compute_stats()


@app.post("/api/shots/clear")
async def api_shots_clear():
    """대시보드 초기화: 발사 로그(shots.db) + 실측 기록(shots.csv) + 발사수 0."""
    from app import db
    from app.config import SHOTS_CSV
    ok = db.clear_shots()
    try:
        if SHOTS_CSV.exists():
            SHOTS_CSV.unlink()
    except OSError as e:
        logger.warning("shots.csv 삭제 실패: %s", e)
    with state.lock:
        state.shots_count = 0
    logger.info("대시보드 초기화: shots 로그/CSV 삭제, 발사수 0")
    return {"ok": ok}


@app.post("/api/ai_report")
async def api_ai_report():
    """발사 로그 집계를 GMS(Claude)로 분석한 텍스트 반환."""
    from app import ai_report, db
    stats = db.compute_stats()
    # 블로킹 호출(수 초)이라 이벤트 루프를 막지 않도록 스레드로 위임
    res = await asyncio.to_thread(ai_report.generate_report, stats)
    return JSONResponse(res, status_code=200 if res.get("ok") else 503)


@app.post("/api/ai_chat")
async def api_ai_chat(payload: dict):
    """발사 로그 챗봇. body: {"messages":[{"role":"user"|"assistant","content":str},...]}

    최근 발사 집계 + 기록을 컨텍스트로 붙여 GMS(Claude)가 질문에 답한다.
    """
    from app import ai_chat, db
    messages = (payload or {}).get("messages") or []
    stats = db.compute_stats()
    shots = db.fetch_shots(limit=100)
    res = await asyncio.to_thread(ai_chat.answer, messages, stats, shots)
    return JSONResponse(res, status_code=200 if res.get("ok") else 503)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=config.network.http_host,
                port=config.network.http_port, reload=False)
