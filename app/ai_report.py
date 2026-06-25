"""발사 로그 AI 분석 (Claude via SSAFY GMS 프록시).

DB 집계를 Claude 에게 넘겨 명중 패턴·약점·보정 방향을 한국어로 요약받는다.
키는 환경변수(GMS_KEY)로만 읽는다. SDK/키가 없어도 서버는 죽지 않고 사유를 반환한다.
"""
from __future__ import annotations

import json
import logging
import os

from app.config import config

logger = logging.getLogger(__name__)

_PROMPT = (
    "너는 AI 자동 조준 대포 시스템의 데이터 분석가야. 아래는 발사 후 ESP32 피에조 "
    "센서로 판정한 로그의 집계야. 명중은 피에조 충격 세기를 0~100 점수(등급 S/A/B/C/D)로 "
    "환산해 기록돼 있어 — 점수가 높을수록 더 강하고 정타에 가까운 명중이야.\n"
    "1) 전체 명중률과 평균/최고 점수·등급 분포에서 보이는 패턴, 2) 거리 구간별 약점, "
    "3) 점수(세기)가 낮은 명중이 많다면 그 원인 가설, 4) tilt_bias/pan_bias 등 보정 방향 "
    "제안을 한국어로 간결하게(불릿 위주) 분석해줘. 데이터가 적으면 그 한계도 분명히 "
    "말해줘.\n\n집계 데이터(JSON):\n"
)


def generate_report(stats: dict) -> dict:
    """집계 통계로 AI 분석 텍스트 생성. {ok, report|reason} 반환."""
    if stats.get("total", 0) == 0:
        return {"ok": False, "reason": "발사 기록이 없습니다. 먼저 발사 데이터를 쌓아주세요."}

    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return {"ok": False,
                "reason": f"{config.ai.api_key_env} 환경변수가 없습니다. "
                          f"키 설정 후 서버를 재시작하세요."}

    try:
        import anthropic
    except ImportError:
        return {"ok": False, "reason": "anthropic SDK 미설치 (pip install anthropic)"}

    try:
        client = anthropic.Anthropic(base_url=config.ai.base_url, api_key=api_key)
        resp = client.messages.create(
            model=config.ai.model,
            max_tokens=config.ai.max_tokens,
            messages=[{
                "role": "user",
                "content": _PROMPT + json.dumps(stats, ensure_ascii=False, indent=2),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return {"ok": True, "report": text,
                "model": resp.model,
                "usage": {"input": resp.usage.input_tokens,
                          "output": resp.usage.output_tokens}}
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 분석 실패: %s", e)
        return {"ok": False, "reason": f"AI 호출 실패: {e}"}
