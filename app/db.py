"""발사+명중판정 로그 저장소 (SQLite).

발사 후 hit_wait_seconds(기본 10s) 동안 ESP32 명중 신호를 기다린 결과(HIT/MISS)와
그 시점의 발사 정보(거리/각도/센서값 등)를 한 행으로 적재한다. 시각화·AI 분석의 원천.

엔진 스레드에서 INSERT, FastAPI 핸들러에서 SELECT 가 일어나므로 매 호출마다 커넥션을
새로 열어 스레드 안전을 보장한다(발사당 1회 쓰기라 비용 무시 가능).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import SHOTS_DB, FEEDBACK_DIR

logger = logging.getLogger(__name__)

# 적재 컬럼(=insert_shot 의 row 키). None 허용.
_COLUMNS = [
    "ts", "ts_iso", "result",
    "distance_m", "distance_mm",
    "pan", "tilt_servo", "used_angle", "model_angle",
    "weight", "air", "tilt_bias",
    "hit_latency_ms", "sensor_value", "esp32_ms",
    "score", "grade",
]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS shots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL,     -- 판정 시각(epoch)
    ts_iso        TEXT,     -- ISO8601(로컬 가독용)
    result        TEXT,     -- 'HIT' | 'MISS'
    distance_m    REAL,     -- 표적 거리(m)
    distance_mm   INTEGER,  -- LiDAR 원시(mm)
    pan           INTEGER,  -- 발사 pan 서보각
    tilt_servo    INTEGER,  -- 발사 tilt 서보각
    used_angle    REAL,     -- 실제 사용 발사각(deg)
    model_angle   REAL,     -- MLP 원시 발사각(deg)
    weight        REAL,     -- 발사체 무게(kg)
    air           REAL,     -- 공기저항
    tilt_bias     REAL,     -- 적용된 tilt 보정(deg)
    hit_latency_ms REAL,    -- 발사~명중신호 지연(ms, MISS면 NULL)
    sensor_value  REAL,     -- ESP32 센서 피크값(보내면 기록, 아니면 NULL)
    esp32_ms      INTEGER,  -- ESP32 millis() (payload.ms)
    score         REAL,     -- 피에조 세기 환산 점수(0~max_score, 값 없으면 NULL)
    grade         TEXT      -- 점수 등급(S/A/B/C/D/F)
);
"""

# 기존 DB(구버전 스키마)에 빠진 컬럼을 보강하기 위한 목록.
_MIGRATE_COLUMNS = {"score": "REAL", "grade": "TEXT"}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(shots)").fetchall()}
    for col, typ in _MIGRATE_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {typ}")


def _connect() -> sqlite3.Connection:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SHOTS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_SCHEMA)
        _migrate(conn)
    logger.info("발사 로그 DB 준비: %s", SHOTS_DB)


def insert_shot(row: dict[str, Any]) -> Optional[int]:
    """한 발의 발사+판정 결과를 적재. row 는 _COLUMNS 의 부분집합이면 된다."""
    ts = row.get("ts", time.time())
    row.setdefault("ts", ts)
    row.setdefault("ts_iso", datetime.fromtimestamp(ts).isoformat(timespec="seconds"))
    cols = [c for c in _COLUMNS if c in row]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO shots ({', '.join(cols)}) VALUES ({placeholders})"
    try:
        with _connect() as conn:
            cur = conn.execute(sql, [row[c] for c in cols])
            return cur.lastrowid
    except sqlite3.Error as e:  # noqa: BLE001
        logger.warning("발사 로그 적재 실패: %s", e)
        return None


def clear_shots() -> bool:
    """모든 발사 로그(shots)를 삭제한다 — 대시보드 초기화용."""
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM shots")
        logger.info("발사 로그 전체 삭제(shots 테이블 비움)")
        return True
    except sqlite3.Error as e:  # noqa: BLE001
        logger.warning("발사 로그 삭제 실패: %s", e)
        return False


def fetch_shots(limit: int = 200) -> list[dict[str, Any]]:
    """최근 발사 기록(최신순)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM shots ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:  # noqa: BLE001
        logger.warning("발사 로그 조회 실패: %s", e)
        return []


def compute_stats() -> dict[str, Any]:
    """집계: 총/명중/명중률, 거리구간별 명중률, 시간순 결과(타임라인)."""
    out: dict[str, Any] = {
        "total": 0, "hits": 0, "misses": 0, "hit_rate": 0.0,
        "by_distance": [], "timeline": [],
        "avg_hit_latency_ms": None,
        "avg_score": None, "best_score": None, "total_score": 0.0,
        "by_grade": [],
    }
    try:
        with _connect() as conn:
            agg = conn.execute(
                "SELECT COUNT(*) n, "
                "SUM(CASE WHEN result='HIT' THEN 1 ELSE 0 END) hits, "
                "AVG(CASE WHEN result='HIT' THEN hit_latency_ms END) lat, "
                "AVG(score) avg_score, MAX(score) best_score, "
                "SUM(COALESCE(score,0)) total_score "
                "FROM shots"
            ).fetchone()
            total = agg["n"] or 0
            hits = agg["hits"] or 0
            out["total"] = total
            out["hits"] = hits
            out["misses"] = total - hits
            out["hit_rate"] = round(hits / total, 4) if total else 0.0
            out["avg_hit_latency_ms"] = (
                round(agg["lat"], 1) if agg["lat"] is not None else None
            )
            out["avg_score"] = round(agg["avg_score"], 1) if agg["avg_score"] is not None else None
            out["best_score"] = round(agg["best_score"], 1) if agg["best_score"] is not None else None
            out["total_score"] = round(agg["total_score"] or 0.0, 1)

            # 등급 분포
            grades = conn.execute(
                "SELECT grade, COUNT(*) n FROM shots WHERE grade IS NOT NULL "
                "GROUP BY grade ORDER BY grade"
            ).fetchall()
            out["by_grade"] = [{"grade": g["grade"], "count": g["n"]} for g in grades]

            # 거리 1m 구간(0-1,1-2,...)별 명중률
            buckets = conn.execute(
                "SELECT CAST(distance_m AS INTEGER) AS bucket, "
                "COUNT(*) n, SUM(CASE WHEN result='HIT' THEN 1 ELSE 0 END) hits "
                "FROM shots WHERE distance_m IS NOT NULL "
                "GROUP BY bucket ORDER BY bucket"
            ).fetchall()
            out["by_distance"] = [
                {
                    "range": f"{b['bucket']}~{b['bucket']+1}m",
                    "shots": b["n"],
                    "hits": b["hits"] or 0,
                    "hit_rate": round((b["hits"] or 0) / b["n"], 4) if b["n"] else 0.0,
                }
                for b in buckets
            ]

            # 최근 50발 타임라인(오래된→최신)
            tl = conn.execute(
                "SELECT id, ts_iso, result, distance_m, score, grade FROM shots "
                "ORDER BY id DESC LIMIT 50"
            ).fetchall()
            out["timeline"] = [dict(r) for r in reversed(tl)]
    except sqlite3.Error as e:  # noqa: BLE001
        logger.warning("발사 로그 집계 실패: %s", e)
    return out
