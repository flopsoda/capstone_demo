"""request_k_electronics_review — Tool Capability of the Group Super Agent: an A2A client.

Same slot as a delegation Tool (reports the handoff as a2a_start / a2a_end and
returns only the peer's final answer), but the peer is another agent system
reached over HTTP, never imported. K's lifecycle events arrive on the same
stream; they are relayed into this run as observation data (marked
`relayed`) for the Group UI and never enter the Group Super's history.
"""

from __future__ import annotations

import os

import httpx
from langchain_core.tools import tool

from ....runtime.a2a import A2A_TIMEOUT, A2AError, request_review
from ....runtime.events import AgentError, current_run, emit
from .. import SYSTEM

FROM_AGENT = "group_super"
PEER_AGENT = "k_super"  # the K Electronics system's Super Agent, as its A2A surface presents it
PEER_SYSTEM = "k_electronics"
PEER_DISPLAY = "K전자"

DEFAULT_K_ELECTRONICS_A2A_URL = "http://localhost:8000/api/a2a/run"


def k_electronics_a2a_url() -> str:
    """K's A2A surface. Unset or empty K_ELECTRONICS_A2A_URL (as .env.example ships it) means the default."""
    return os.environ.get("K_ELECTRONICS_A2A_URL", "").strip() or DEFAULT_K_ELECTRONICS_A2A_URL


K_ELECTRONICS_A2A_URL = k_electronics_a2a_url()


def make_client() -> httpx.Client:
    """HTTP client for the peer. Tests replace this with an in-process client."""
    return httpx.Client(timeout=A2A_TIMEOUT)


@tool
def request_k_electronics_review(request: str) -> str:
    """K전자(계열사) 슈퍼에이전트에게 A2A로 요청한다. K전자가 가진 기록과 K전자의 판단은 모두 이 Tool로 받는다:
    - K전자에서 열린 회의(경영회의 · 전략회의 등)의 기록과 결정사항, 그 회의에서 배정된 후속 업무(Action Item)와 진행 상황
    - K전자가 추진 · 검토 중인 인수안 · 투자안의 재무 · 전략 · 기술 타당성, 인수가격 등 조건을 바꾼 What-if 검토, 본실사 진입 여부
    - K전자와 관련된 외부 리스크 · 뉴스 분석
    - 그룹이 K전자에 배정한 업무의 전달(K전자 담당자가 확인해야 정식 업무가 된다)과 그 진행 조회,
      K전자 후속 업무의 담당자에게 보내는 리마인더(진행 상황 확인) 메일
    그룹 회의체가 연 회의의 기록이나 그룹 차원의 자문은 담당하지 않는다 (그룹의 미팅 · 경영자문 에이전트가 담당).

    Args:
        request: K전자 슈퍼에이전트가 이전 대화 없이도 바로 수행할 수 있는 자기완결적 요청 (한국어).
            대상 기업 · 목적 · 필요한 항목 · 조건(예: 인수가격)을 구체적으로 쓴다.
            업무 전달이면 "업무 요청:"으로 시작해 업무 내용 · 기한 · 담당 지정 · 그룹 쪽 기록 id(예: MTG-2026-10-20-01-A2)를,
            리마인더면 대상 업무(업무명 · 담당자)와 "담당자에게 진행 상황 확인 메일을 보내 달라"를 명시한다.
    """
    run = current_run()
    trace_id = (run.trace_id or run.run_id) if run is not None else None
    emit({"type": "a2a_start", "from": FROM_AGENT, "to": PEER_AGENT, "request": request})
    try:
        with make_client() as client:
            response = request_review(
                client,
                K_ELECTRONICS_A2A_URL,
                request,
                caller=SYSTEM,
                trace_id=trace_id,
                principal=run.principal if run is not None else None,
                principal_token=run.principal_token if run is not None else None,
                observe=lambda event: emit({**event, "relayed": True}),
            )
    except A2AError as e:
        detail = f"{PEER_DISPLAY} {e.peer_agent}: {e}" if e.peer_agent else f"{PEER_DISPLAY}: {e}"
        raise AgentError(PEER_AGENT, detail) from e
    emit({"type": "a2a_end", "from": FROM_AGENT, "to": PEER_AGENT, "response": response})
    return response
