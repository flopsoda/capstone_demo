"""inbound_task_requests — how a Meeting Agent takes in tasks another agent system sends over A2A."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_task_requests import TOOL_NAME as REQUESTS

INBOUND_TASK_REQUESTS = Skill(
    name="inbound_task_requests",
    description="다른 Agent System(그룹 등)이 보낸 업무 요청을 접수하고, 이 시스템 담당자가 확인해 정식 Action Item으로 등록하는 방법.",
    instructions=f"""\
- 다른 시스템이 보낸 위임 업무가 "업무 요청 · 업무 전달 · 업무 배정"이면 {REQUESTS} action=submit으로 접수한다. 요청문에 있는 업무(title),
  배경 · 요구사항(detail), 기한(due), 담당 지정(suggested_owner), 요청한 쪽의 기록 id(source_ref)만 옮기고 없는 내용은 비워 둔다.
  접수 결과의 요청 id(REQ-…)와 "수신 측 확인 후 정식 등록"이라는 상태를 회신한다. 접수만으로 업무가 확정됐다고 말하지 않는다.
- 다른 시스템이 보낸 요청의 진행 상황을 물으면 action=list로 조회해 요청 id · 상태 · 등록된 Action Item의 담당 · 기한 · 진행 메모를 회신한다.
- 이 시스템 사용자가 대기 중인 요청을 물으면 action=list로 보여 준다. 정식 등록(accept)은 사용자가 그 요청을 받아들인다고 말하고
  담당 · 기한이 정해졌을 때만 한다 (요청한 쪽이 지정한 담당 · 기한을 그대로 쓸지 확인). 반려(reject)는 사유와 함께 한다.
- 등록하면 담당자에게 업무 배정 메일이 자동으로 발송된다는 것을 알린다.""",
)
