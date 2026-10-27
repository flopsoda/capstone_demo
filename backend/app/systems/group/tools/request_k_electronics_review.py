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

K_ELECTRONICS_A2A_URL = os.environ.get("K_ELECTRONICS_A2A_URL", "http://localhost:8000/api/a2a/run")


def make_client() -> httpx.Client:
    """HTTP client for the peer. Tests replace this with an in-process client."""
    return httpx.Client(timeout=A2A_TIMEOUT)


@tool
def request_k_electronics_review(request: str) -> str:
    """K전자 슈퍼에이전트(K Electronics Super Agent)에게 A2A로 검토를 요청한다.
    담당: K전자 계열사 차원의 검토 — K전자의 과거 경영회의 결정사항, K전자가 검토 중인 인수안·투자안의
    재무·전략·기술 타당성, K전자와 관련된 외부 리스크, 본실사 진입 여부처럼 K전자 내부 자료와 K전자의 판단이 필요한 항목.
    그룹 자체의 회의 기록이나 그룹 차원의 자문은 담당하지 않는다.

    Args:
        request: K전자 슈퍼에이전트가 수행할 검토 요청을 자연어로 서술한 문장 (한국어). 대상 기업과 포함할 항목을 명시한다.
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
                observe=lambda event: emit({**event, "relayed": True}),
            )
    except A2AError as e:
        detail = f"{PEER_DISPLAY} {e.peer_agent}: {e}" if e.peer_agent else f"{PEER_DISPLAY}: {e}"
        raise AgentError(PEER_AGENT, detail) from e
    emit({"type": "a2a_end", "from": FROM_AGENT, "to": PEER_AGENT, "response": response})
    return response
