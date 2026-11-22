"""query_enterprise_kg — Tool Capability factory: the Advisory Agent's view of the system's Enterprise KG.

A system binds its KG with make_query_enterprise_kg(KG_STORE). Who asks comes from the run (meeting_access), never
from the model: records of meetings the user may not see are not read. The graph is kept up to date by the
system's background job (kg.store.KGStore.watch_pass) and the build CLI; this Tool only reads it.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.kg.store import KGStore
from .meeting_access import current_viewer, store_call

TOOL_NAME = "query_enterprise_kg"


def make_query_enterprise_kg(kg: KGStore) -> BaseTool:
    @tool(TOOL_NAME)
    def query_enterprise_kg(query: str, entities: list[str] | None = None) -> dict[str, Any]:
        """회의 기록과 뉴스를 잇는 기업 지식 그래프에서 기업 · 인물에 관해 알려진 것을 조회한다. 결과는 근거별로 나뉜다:
    decisions(그 기업을 다룬 확정 회의록의 결정 · 기준과 전제 · 조건, 바뀐 결정은 superseded) · follow_ups(그 회의의 후속 업무),
    reported_facts(기사가 전한 기업 간 관계 — 뉴스 사건별, 상태 active · ended와 기간, 근거 기사),
    assessed_impacts(결정 감시가 기사와 결정의 전제를 대조해 판정한 영향: effect broken · at_risk),
    associations(같은 기업을 다루는 뉴스 사건과 결정 — 영향 여부를 판정하지 않은 단순 연관), news_events, meetings.
    열람 권한이 없는 회의의 기록은 나오지 않는다. 새 뉴스를 수집하거나 분석하지 않는다(저장된 기록만 연결해 보여 준다).

    Args:
        query: 확인하려는 내용을 받은 그대로 (예: "뉴로엣지 인수 검토 — 최근 뉴스와 지난 회의 기준").
        entities: 조회할 기업 · 인물 이름 (예: ["뉴로엣지"]). 비우면 query에 나온 이름으로 찾는다.
    """
        viewer = current_viewer()
        return store_call(lambda: kg.query(viewer, query, entities))

    return query_enterprise_kg
