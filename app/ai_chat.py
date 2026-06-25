"""발사 로그 챗봇 (Claude via SSAFY GMS 프록시).

대시보드에서 사용자가 로그에 대해 자유롭게 질문하면, 발사 로그 집계 + 최근 기록을
컨텍스트로 붙여 Claude 가 한국어로 답한다. 대화 기록(messages)을 받아 후속 질문도 지원.
키는 환경변수(GMS_KEY)로만 읽고, 키/SDK 가 없어도 서버는 죽지 않고 사유를 반환한다.
"""
from __future__ import annotations

import json
import logging
import os

from app.config import config

logger = logging.getLogger(__name__)

_SYSTEM = (
    "너는 'AI 자동 조준 대포' 시스템의 발사 로그 분석 어시스턴트야. "
    "아래 [집계]와 [최근 기록]만을 근거로 사용자 질문에 한국어로 간결히(필요하면 불릿) 답해. "
    "수치는 정확히 인용하고, 데이터에 없는 건 추측하지 말고 모른다고 해. "
    "명중 점수는 피에조 충격 세기를 0~100점(등급 S/A/B/C/D)으로 환산한 값이야.\n\n"
)


def answer(messages: list, stats: dict, shots: list) -> dict:
    """대화 메시지 + 로그 컨텍스트로 Claude 답변 생성. {ok, reply|reason, usage} 반환."""
    safe = [
        {"role": "assistant" if m.get("role") == "assistant" else "user",
         "content": str(m.get("content", ""))}
        for m in (messages or []) if str(m.get("content", "")).strip()
    ]
    if not safe:
        return {"ok": False, "reason": "질문이 비어 있습니다."}

    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return {"ok": False, "reason": f"{config.ai.api_key_env} 환경변수가 없습니다."}
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "reason": "anthropic SDK 미설치 (pip install anthropic)"}

    system = (_SYSTEM
              + "[집계]\n" + json.dumps(stats, ensure_ascii=False)
              + "\n\n[최근 기록]\n" + json.dumps(shots, ensure_ascii=False))
    try:
        client = anthropic.Anthropic(base_url=config.ai.base_url, api_key=api_key)
        resp = client.messages.create(
            model=config.ai.model,
            max_tokens=config.ai.max_tokens,
            system=system,
            messages=safe,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return {"ok": True, "reply": text, "model": resp.model,
                "usage": {"input": resp.usage.input_tokens,
                          "output": resp.usage.output_tokens}}
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 챗봇 실패: %s", e)
        return {"ok": False, "reason": f"AI 호출 실패: {e}"}
