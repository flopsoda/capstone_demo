# Architecture

이 문서는 이 repository의 architecture **Single Source of Truth**다.
"우리가 무엇을 만들고 있는지, 현재 어디까지 구현되어 있는지, 앞으로 어떤 구조를 지향하는지"를 설명한다.

문서 전체에서 **Current implementation**(지금 코드에 있는 것)과 **Target architecture**(지향하는 구조)를 구분해서 적는다.
둘이 다른 곳은 [10. 현재 구현과 목표 구조의 차이](#10-현재-구현과-목표-구조의-차이)에 모아 두었다.

기준 시점: 2026-10-27 (Phase 8까지 완료: 두 Agent System backend + 제품 UI 둘 + Demo Monitor, mock scenario · 옛 단일 앱 제거. Step 1~8 전부 real.
이후 미팅 에이전트의 회의 기록 업무 — Postgres 회의 저장소, 회의록 작성 · 진행 추적 · 일정 Tool, 요청자(principal) 전달 — 추가: 11장 12 · 13번 결정).

---

## 1. 프로젝트 목적

대기업 C-level 경영진을 위한 **Multi-Agent Decision Support System / Agent Platform의 prototype**이다.
단순한 M&A 챗봇이나 고정 workflow demo가 아니다.

- 경영진은 자연어로 요청한다.
- 그룹 Super Agent가 요청을 이해하고 계열사 Super Agent에게 A2A로 검토를 요청한다.
- 계열사 Super Agent는 자기 전문 Agent들에게 업무를 위임하고 결과를 종합한다.
- 전문 Agent는 자신에게 허용된 Workflow Tool로 실제 데이터를 조회·분석한다.

현재는 이 구조를 **NeuroEdge 인수 검토**라는 대표 요청으로 시연한다. 처음에는 mock으로 재생되던 단계를 한 Step씩 실제 Agent 실행으로 교체했고,
지금은 모든 Step이 실제 실행이며 mock 경로는 없다 ([7장](#7-개발-전략-vertical-slice)).

---

## 2. 전체 Multi-Agent 구조

### 개념 구조 (Target)

```text
User / Executive
       │
       ▼
Group Super Agent
       │
       │ A2A
       ▼
Subsidiary Super Agent
       │
       ├── Meeting Agent ──▶ run_meeting_context_retrieval
       ├── News Agent ─────▶ run_company_risk_news_analysis
       └── Management Advisory Agent ──▶ run_mna_review
                │
                ▼
       Enterprise / External Data
```

### 현재 demo scenario의 구조

```text
그룹 회장 (Chairman)
       ↓
그룹 슈퍼에이전트 (Group Super)          ← REAL (Group system, :8001; Group UI `/group`)
       ├── delegation → 그룹 미팅 / 뉴스 / 경영자문 에이전트 (Group 데이터)   ← REAL (Phase 5b)
       ⇅ A2A (HTTP)                      ← REAL (Group Tool → K `/api/a2a/run`; UI는 relay된 K lifecycle로 A2A 세션을 그림)
K전자 슈퍼에이전트 (K Electronics Super)  ← backend REAL (K system, :8000)
       ↓ delegation
       ├── 미팅 에이전트 (Meeting)         ← backend REAL (LangGraph)
       ├── 뉴스 에이전트 (News)            ← backend REAL (LangGraph)
       └── 경영자문 에이전트 (Advisory)    ← backend REAL (LangGraph)
```

Agent 식별자는 backend와 frontend가 같은 문자열을 쓴다:
`chairman`, `group_super`, `k_super`, `meeting`, `news`, `advisory`
(frontend: `src/types/agent.ts`의 `AgentId`, backend: 각 agent 모듈의 `AGENT_ID`와 각 system `registry.py`의 `allowed_agents`).

---

## 3. 핵심 개념

### 3.1 Super Agent

Super Agent는 단순 workflow router가 아니다.
자연어 요청을 이해하고, **어떤 전문 Agent에게 무엇을 맡길지 스스로 판단**하며, 전문 Agent에게 **자연어로** 업무를 위임한다.

```text
K Electronics Super
    ├─ delegate_to_meeting_agent(task: str)
    ├─ delegate_to_news_agent(task: str)
    └─ delegate_to_advisory_agent(task: str)
```

- 별도의 중앙 Planner / Router 노드가 판단을 대신하는 구조는 기본 architecture로 두지 않는다. **Agent 자체가 판단 주체**다.
- 위임은 LLM의 실제 tool calling으로 일어난다. 위임 문장(`task`)은 LLM이 작성하므로 실행마다 달라질 수 있다.
- Super Agent는 직접 분석하거나 답을 지어내지 않는다 (K Super system prompt 규칙).

**Current implementation** — `backend/app/runtime/agent_core.py`(Super core) + `backend/app/systems/k_electronics/agents/k_super.py`(정체성)
- Phase 1(2026-10-12)에서 Specialist와 같은 방식으로 **공통 super core**를 두었다: `agent_core.SuperAgent(agent_id, display_name, system_prompt, tools)`,
  `build_super_graph(spec)`(LangGraph `StateGraph(MessagesState)`, 노드 `model` / `tools`), `run_super(spec, graph, request, invocation=...)`.
  `k_super.py`에는 `AGENT_ID` · `IDENTITY` · Registry 조회 · `SPEC` · `build_k_super()` · `run_k_super()`만 있다.
- 호출 맥락(invocation)은 `"a2a"`(다른 Agent System의 Super Agent가 보낸 요청, 기본값) | `"direct"`(사용자 직접 대화)이며 `agent_core.SUPER_INVOCATION_CONTEXT` 한 문단으로 모델에게 알린다.
  근거 검사(위임 1회 이상)는 `a2a`에서만 한다. API: `POST /api/a2a/run`이 `a2a`, `POST /api/agents/k-super/run`이 `direct` (Phase 4).
- Registry가 허용한 Capability: Skill `specialist_delegation`(위임 지침), Tool `delegate_to_meeting_agent`, `delegate_to_news_agent`, `delegate_to_advisory_agent`. Skill은 `backend/app/library/skills/`, 위임 Tool은 `backend/app/systems/k_electronics/tools/`에 있다 ([4장](#4-capability-registry)).
- `tools → model` 루프. 위임 결과(`ToolMessage`)를 받은 모델이 다시 판단해 다음 위임을 결정한다. 위임 순서와 `task` 문장은 모델이 정한다.
  Skill이 "한 번에 하나의 전문 에이전트에게만 위임하고 결과를 받은 뒤 다음을 판단"하도록 지시하므로 현재 scenario에서는 Meeting → News → Advisory가 **순차**로 일어난다 (병렬 위임 없음).
- **이전 결과의 전달은 모델의 판단이다.** Skill이 "앞서 받은 결과 중 이번 업무에 필요한 사실이 있으면 위임 문장에 담으라"고 지시할 뿐,
  Meeting / News 결과를 추출해 고정 template에 끼워 넣는 코드는 없다. Advisory 위임 `task`에 회의 기준과 외부 리스크가 어떻게 들어가는지는 실행마다 다를 수 있다.
- **종료 조건은 모델의 판단이다.** `tools` 노드 뒤에는 무조건 `model`로 돌아가는 엣지만 있다 (`graph.add_edge("tools", "model")`).
  매 `model` 턴에서 모델은 원 요청과 지금까지 받은 전문 Agent 결과를 보고 (a) 위임 Tool을 호출하거나 (b) Tool 호출 없이 최종 응답을 쓴다.
  (b)이면 `route_on_tool_calls`가 `END`로 보내고, 그 응답이 `run_k_super()`의 반환값이자 `agent_end(k_super).response`(Step 6)다.
  "등록된 위임 Tool을 모두 호출했는가"를 보는 코드는 없다. Registry의 Tool 목록은 checklist가 아니며, 요청에 따라 일부 전문 Agent만 쓸 수 있다
  (`tests/test_agents.py`: 회의 결정사항만 묻는 요청은 Meeting 위임 한 번 뒤 바로 답한다).
- 종합은 K Super LLM이 자기 message history(원 요청 + 위임 `ToolMessage`들)를 보고 쓴다. 별도 Synthesis Tool / Agent, 결과 aggregation 구조, 고정 template은 없다.
  어떤 항목을 어떻게 쓰는지는 실행마다 다를 수 있다. 출력 형식 규칙(한국어, 마크다운 없이 줄바꿈과 "- " 목록)은 `IDENTITY`에 있다.
- 남은 검사는 근거 검사 둘뿐이다: 위임 Tool을 하나도 호출하지 않고 응답하면(K Super가 스스로 지어낸 답) `AgentError("k_super", ...)`,
  최종 응답이 비어 있으면 `AgentError`. mock으로 대체하지 않는다.

### 3.2 Specialist Agent

Meeting / News / Advisory Agent는 함수가 아니라 **각각 독립된 conversational Agent**다.

```text
자연어 요청 수신
    ↓
요청 이해 (LLM)
    ↓
허용된 capability/tool 중 필요한 것 선택 (LLM tool calling)
    ↓
Workflow Tool 실행
    ↓
결과 해석 (LLM)
    ↓
자연어 응답
```

같은 Agent core가 두 경로에서 쓰인다:

```text
Super Agent → Specialist Agent   (delegation)
User        → Specialist Agent   (직접 대화)
```

**Current implementation** — `backend/app/runtime/agent_core.py` + `backend/app/systems/k_electronics/agents/{meeting,news,advisory}.py` (Group도 같은 core: `systems/group/agents/`)
- 세 Agent는 `runtime/agent_core.py`의 **공통 specialist core** 하나를 쓴다: `Specialist(agent_id, display_name, system_prompt, tools)` 값 객체,
  `build_specialist_graph(spec)`(LangGraph `StateGraph(MessagesState)`, `model ⇄ tools` 루프), `run_specialist(spec, graph, task, invocation=...)`.
  Agent 파일에는 `AGENT_ID` · `IDENTITY` · Registry 조회(`SYSTEM_PROMPT`, `TOOLS`) · `build_*_agent()` · `run_*_agent()`만 있다.
- Registry가 허용한 Capability:
  - Meeting: Skill `meeting_context_analysis`(조회·정리 · 브리핑 지침), `meeting_minutes_writing`(transcript → 초안 → 확정 지침), `meeting_follow_up`(진행 추적 · 갱신 · 일정 지침),
    Tool `run_meeting_context_retrieval`, `save_meeting_minutes`, `run_action_item_tracking`, `update_action_item`, `run_meeting_schedule` ([3.3](#33-workflow-tool)).
  - News: Skill `external_risk_analysis`(외부 리스크 조사·정리 지침, 응답 형식), Tool `run_company_risk_news_analysis`.
  - Advisory: Skill `mna_feasibility_review`(인수 검토 지침 — 관점 선택, 맥락 전달, 응답 형식), Tool `run_mna_review`.
- **호출 맥락(invocation)** 은 `"delegation" | "direct"` 두 가지다. 같은 그래프가 쓰이고, 호출 맥락은 LangGraph `config["configurable"]`로 실행마다 전달된다 (state에 넣지 않는다).
  - 모델에게는 system context 끝에 한 문단(`agent_core.INVOCATION_CONTEXT`)으로 알려 준다: delegation이면 "슈퍼에이전트에게 돌아가는 보고이므로 Tool 결과에 근거하라", direct면 "인사·개념 질문에는 Tool 없이 답하고 데이터가 필요한 업무면 Tool을 쓰라".
  - delegation(`delegate_to_*` Tool 안, 기본값)에서 Workflow Tool 없이 답하면 `AgentError(agent_id, ...)`. 데이터에 근거하지 않은 보고서가 K Super에 올라가면 안 되기 때문이다.
  - direct(`POST /api/agents/{meeting,news,advisory}/run`)에서는 이 검사를 하지 않는다. Tool 호출 여부는 모델이 Skill 지침에 따라 결정한다.
    예: 경영자문 탭에서 "earn-out이 뭐야?"는 Tool 없이, "뉴로엣지 인수를 CTO 관점에서 검토해줘"는 `run_mna_review`로 답한다 (`tests/test_agents.py`).
- 전문 Agent는 Tool 호출이 항상 강제되는 함수형 Agent가 아니라 독립적으로 대화 가능한 conversational Agent다. 위 검사는 delegation 경로의 보고 품질 보호 장치일 뿐이다.

### 3.3 Workflow Tool

Workflow Tool은 **Agent가 아니다**.
전문 Agent가 실제 업무를 수행하기 위해 사용하는 **고정적이거나 반고정적인 업무 절차**이며, Registry 관점에서는 **Tool Capability**다 ([4장](#4-capability-registry)).

```text
Meeting Agent   → run_meeting_context_retrieval · save_meeting_minutes · run_action_item_tracking · update_action_item · run_meeting_schedule
                  · manage_mail_outbox · run_task_requests (K만) · correct_transcript · run_decision_review (Group만)
News Agent      → run_company_risk_news_analysis
Advisory Agent  → run_mna_review
```

- Agent는 **어떤** capability를 쓸지 판단한다. 선택된 Tool **내부**의 반복적·결정적 절차는 LLM 자유 추론에 맡기지 않는다.
- `run_mna_review` 내부에는 `입력 정보 정리 → persona별 평가 기준 적용 → 항목별 평가 → 긍정/우려 요인 → 권고 → 조건` workflow가 캡슐화되어 있다.
  이 내부 단계를 Agent-visible Tool 여러 개로 쪼개지 않는다.
- Workflow Tool은 데이터 경계이기도 하다. Agent는 fixture / DB를 직접 만지지 않는다.

**Current implementation** — Agent-visible Tool과 내부 workflow를 분리한다.
- **회의 저장소 (2026-10-27).** 미팅 Tool 다섯 개는 Postgres 회의 저장소를 쓴다. `library/workflows/meeting_store.MeetingStore(schema, seed)`가 system마다 하나 있고
  (`systems/<system>/__init__.py`의 `MEETING_STORE`: K는 schema `kelec`, Group은 `grp`), 연결은 `MEETINGS_DATABASE_URL`이다. 처음 쓸 때 schema(`meeting_schema.sql`, 테이블 15개)가 없으면 만들고,
  회의가 하나도 없으면 그 system의 `data/meetings.json`으로 채운다 (`MEETINGS_SEED_MEETINGS=0`이면 예시 회의 없이 빈 채로 시작하고 임직원 연락처만 넣는다 — 2026-11-04). 배포에서는 system마다 DB role이 따로라 Group은 K의 schema를 읽을 수 없다.
  Tool factory는 `path` 대신 store를 받는다: `make_<tool>(store)`. workflow 함수는 `store`와 `viewer`를 인자로 받는 함수이고 LLM 없이 테스트한다 (`tests/test_meeting_records.py`, 테스트 DB 필요).
- **요청자(viewer).** 모든 미팅 workflow는 `Viewer(email, name, groups, clearance, can_write)`로 거른다. Tool은 이를 모델 인자가 아니라 run에서 읽는다
  (`library/tools/meeting_access.current_viewer()` ← `RunContext.principal` · `origin`, [6장](#6-backend와-frontend의-책임)). 등급 규칙은 gateway `shared/access.py`와 같다:
  `classification <= clearance AND (allowed_groups IS NULL OR 겹침)`을 SQL 안에서 먼저 적용하므로 볼 수 없는 회의는 읽히지도 않는다.
  principal이 없는 요청(로컬 UI · 테스트)은 `MEETINGS_ANONYMOUS_CLEARANCE`(배포 0, 로컬 예시 4)를 쓴다. 다른 system의 A2A 요청으로 시작된 run(`origin = "a2a"`)은 읽기만 한다.
- `run_meeting_context_retrieval(query, topics, meeting_id?, date_from?, date_to?)` → `library/workflows/meeting_context_retrieval.retrieve_meeting_context`:
  열람 가능한 회의 중 **확정 회의록**만(id로 한 회의를 볼 때는 초안 포함) 키워드 점수(topic 3점, title 2점, decision 1점, 절대/상대 threshold — 기존 그대로)로 고른다.
  반환 dict의 기존 key(`matched`, `meetings[].id · date · title · decisions[{speaker, statement}] · open_action_items[{text, owner}]`)는 유지하고
  `summary`, 결정의 `ref`(예: `MTG-2026-09-15-D1`) · `kind`, `open_issues`, Action Item의 `id · due · status · overdue_days`를 더했다 (frontend 렌더 · gateway 감사로그 호환).
- `save_meeting_minutes(title, date, summary, decisions?, action_items?, attendees?, topics?, body?, classification, location?, meeting_id?, finalize, correction_reason?)` → `meeting_minutes.save_minutes`:
  처음엔 초안, `finalize=true`면 확정하되 담당 · 기한이 빠진 Action Item이 있으면 확정하지 않고 `warnings`로 알린다. 확정본은 `correction_reason`이 있을 때만 정정되고
  이전 버전은 `minutes_revisions`에 snapshot으로 남으며, 기존 Action Item은 지우지 않는다. 결정은 `decision | resolution(outcome · 찬반 수 · dissents) | criterion | opinion | issue`,
  이전 결정을 바꾸면 `supersedes`, 쟁점을 해결하면 `resolves`(ref). 사람 · 조직 이름은 디렉터리(`people` · `org_units`)에서 찾고 없으면 등록해 `new_directory_entries`로 알린다.
- `run_action_item_tracking(query?, owner?, status?, due_before?, due_within_days?, overdue_only, meeting_id?, mine)` → `action_items.track_action_items`: 회의를 가로질러
  지연 일수 큰 순 → 기한 순, 같은 범위의 상태별 건수(`summary`)와 기준일(`as_of`, 요일 포함 — 모델은 오늘 날짜를 모른다)을 준다. 조회만 한다.
- `update_action_item(item_id, status?, note?, due?, owner?, remind?)` → `action_items.update_action_item`: 변경마다 `action_item_updates`에 누가 · 언제 · 이전/새 상태 · 메모를 남긴다. 모르는 id면 `candidates`. `remind=true`는 사용자가 독촉을 요청할 때 담당자에게 지금 확인 요청 메일을 보낸다 (`outbox.queue_nudge_mail`: 확정된 회의록의 열린 할 일 · 사람 담당 · 확정 권한, 할 일마다 하루 한 번, 매일 아침 기한 알림과 별개).
- `run_meeting_schedule(action = list | draft | create | update | cancel, ...)` → `meeting_schedule.meeting_schedule`: 외부 캘린더 동기화 없는 사내 일정.
  `draft`(2026-11-04)는 저장 · 메일 없이 예약 요청을 점검한다: 같은 주제의 지난 회의에서 참석자 · 장소 · 이어지는 안건을 채우고(assumed), 빠진 것(missing: title · date · start · attendees), 여러 사람에 맞거나 기록에 없는 이름(unclear, 후보), 겹치는 회의(conflicts)를 돌려준다. 끝 시각이 없으면 1시간. Skill은 complete면 바로 create, 아니면 빠진 것만 묻고 화면이 예약 양식을 띄운다 (gateway `schedule_form` 카드, 관제의 '+ 회의 예약'과 같은 양식 → desk `schedule` create).
  `create`의 `carry_over_from`은 이전 회의의 보류 안건과 미결 쟁점(`issue`)을 새 회의 안건으로 이월한다. 시각은 한국 시간이며 `list`는 `today`(요일 포함)를 준다.
  예약된 회의의 회의록은 같은 id로 `save_meeting_minutes`에 쓴다.
- **회의 후속 기능 (2026-10-28, 11장 결정 14~19).**
  - **schema migration.** `meeting_schema.sql`이 version 1이고, `library/workflows/meeting_migrations/NNN_name.sql`을 `MeetingStore`가 처음 연결할 때 순서대로 적용해
    schema마다 `schema_migrations`에 남긴다 (`002_delivery_and_review.sql`: `transcripts.source_ref`, `compliance_flags`, `outbox`, `task_requests`; `003_outbox_reject_without_address.sql`: 주소 없는 메일도 반려 가능; `004_autonomous_mail.sql`: `staff_directory`, 승인 대기 메일을 발송 대기로; `005_attendance.sql`: 회의별 목소리 라벨 `people.speaker_meeting_id`, 출석 확인 `checkin_sessions` · `checkin_attendees`, `meeting_participants.checked_in_at`; `006_decision_watch.sql`: 결정의 전제 · 조건 `decision_premises`, 그 전제를 흔든 기사 · 보고 `premise_signals`, 메일 종류 `alert`; `007_subjects.sql`: 회의 주제 `meetings.subject`). 적용된 파일은 고치지 않고 다음 번호를 추가한다.
  - **확정 권한.** 회의록 확정 · 정정, Compliance 검토, 메일 재발송 · 취소 · 주소 정정, 업무 요청 접수는 `meeting_store.can_confirm`: 그 회의의 의장 · 간사(참석자 role, `people.email` = 요청자 email)
    또는 `MEETINGS_CONFIRM_GROUPS`(기본 `secretary,executive`, 로컬 데모는 `*`) 그룹. 참석자 판단은 그 호출의 참석자 변경 **전** 상태로 한다. 권한이 없으면 초안으로 저장하고 `warnings`로 알린다 (정정은 거부).
  - **Compliance.** `compliance.py`의 결정적 규칙(미공개 중요정보 · 담합 · 부정청탁 · 회계 부정 · 기록 은폐 · 부당노동행위 · 개인정보)이 회의록 요약 · 본문 · 결정과 연결된 transcript 발언을 검사해
    `compliance_flags`(open)를 만든다. open이 있으면 확정하지 않는다. `save_meeting_minutes(flag_reviews=[{id: "CF-n", status: cleared | acknowledged, note}])`로 확정 권한자가 검토한다
    (acknowledged는 조치 내용 note 필수). 문장을 고쳐 다시 저장하면 그 open 항목은 다시 검사되어 사라진다. 판정이 아니라 검토 요청이다.
  - **transcript.** speech-service가 회의 녹음(화자 라벨 transcript)을 30일 보관하며 id를 텍스트 첫 줄(`[transcript_id: TR-…]`)에 붙인다. 모델은 `save_meeting_minutes(transcript_id=)`로 id만 넘기고
    `transcripts.py`가 내부망(`SPEECH_SERVICE_URL`, Bearer `SPEECH_API_KEY`)으로 가져와 `transcripts` · `transcript_segments`에 복사한다(만료 `MEETINGS_TRANSCRIPT_RETENTION_DAYS`, 기본 365일).
    결정 · Action Item의 `source_time`("03:15")이 그 발언에 연결된다. 가져오지 못하면 경고만 하고 저장은 한다.
  - **메일 발송함.** `outbox.py`: 확정 · 정정 → 참석자 회의록 메일 + 사람 담당자 업무 배정 메일, `run_meeting_schedule` create · update · cancel → 참석자 안내 메일, 매일 아침 → 기한 알림
    (D-3 · D-1 · 당일 · 경과 다음 날부터 매주, 아직 안 보낸 이전 알림은 대체). **Agent가 주소를 스스로 찾아 승인 없이 바로 보낸다** (2026-10-30, 11장 17번):
    `approved`(`approved_by = 'agent'`)로 쌓이고 배경 작업이 발송한다. 받는 주소는 `people.email` → gateway 디렉터리(`DIRECTORY_URL`, Bearer `INTERNAL_API_KEY`)에서 이름이 정확히 한 명인
    로그인 등록 이메일 → 임직원 · 외부 연락처 `staff_directory`(system fixture `data/meetings.json`의 `directory`로 seed; 이름이 같고 직함이 맞는 한 명, 이름 없이 역할로만 기록된 사람은
    "외부 자문위원 (OO회계법인)"처럼 그 기관에서 그 역할을 가진 한 명) 순으로 찾고, 찾은 주소는 `people.email`에 남긴다. 주소를 추측하지 않는다: 아무 데서도 못 찾으면 `needs_address`로 두고
    매일 아침 다시 찾는다. 목소리가 등록되지 않은 발언자("화자 A" · "화자 B" …, 예전 변환본은 "미확인 화자 N")에게는 메일을 만들지 않고, 그런 라벨이 담당인 할 일은 실제 담당자를 정해야 확정된다. 사람은 실패한 메일 다시 보내기(`resend`) · 안 보낸 메일 취소(`reject`) · 주소 정정(`set_address`, 바로 다시 보냄)만 한다.
    부서 담당 Action Item은 주소가 없어 배정 메일을 만들지 않고 그렇게 알린다.
  - **메일로 진행 보고 (2026-11-04, `mail_actions.py`).** 업무 배정 · 기한 알림 · 독촉 메일은 HTML로도 보내고 **완료 · 진행 중 · 막힘** 버튼을 붙인다 (텍스트 본문에는 같은 링크).
    버튼은 gateway의 로그인 없는 화면 `MEETINGS_ACTION_URL/<token>?s=<상태>`를 연다 (Group만 이 값을 가짐; 없거나 `PRINCIPAL_SIGNING_KEY`가 없으면 버튼 없이 보냄).
    token = 할 일 id · 받는 사람(person id) · 만료(30일) + `PRINCIPAL_SIGNING_KEY`에서 파생한 키의 HMAC — 회사 계정이 없는 외부 담당자도 쓸 수 있게 하려는 것이다.
    링크를 여는 것(GET)은 할 일을 보여 주기만 하고(회사 메일의 보안 검사가 링크를 미리 열기 때문, RFC 8058의 원클릭 구독 취소와 같은 이유), 화면에서 '보내기'(POST)를 눌러야
    gateway → `POST /api/mail-action {token, status, note}` → `mail_actions.act`가 상태를 바꾸고 `action_item_updates`에 그 담당자의 이메일로 "메일로 보고: 메모"를 남긴다.
    막힘은 메모(이유)가 있어야 하고, 할 일의 담당이 바뀌면 이전 담당자의 링크는 쓸 수 없다. 관제의 활동에는 상태 변경과 메일 보고가 "진행 보고 — 누가: 상태 · 할 일 (메일) — 메모"로 보인다.
    회신 메일을 읽어 반영하는 것(메일 수신)은 아직 없다.
  - **출석 확인 · 참석자 확인 (2026-11-03, `attendance.py`).** 녹음만으로는 말하지 않은 참석자를 알 수 없다. 회의지원 채팅에 녹음을 올리면 화면이 출석 확인(`CHK-…`)을 열고
    30초마다 바뀌는 코드(HMAC)를 담은 QR을 띄운다. 참석자가 휴대폰으로 찍으면 gateway가 로그인 계정(이메일 · 이름 · Keycloak id)으로 출석시킨다 (지금 코드와 직전 3개까지 유효, 3시간 뒤 마감).
    회의록 요청 문장에 그 코드가 들어가고 `save_meeting_minutes(checkin_code=)`가 받는다 (없으면 같은 사람이 12시간 안에 연 출석 확인). 출석한 계정은 말하지 않았어도 참석자(`attended`, `checked_in_at`)가 되고,
    모델이 이름으로만 적은 같은 이름의 사람은 그 계정으로 합친다. 저장 결과의 `attendance`(출석 · 발언 여부, 이름 모를 목소리, 말하지 않은 참석자, 목소리 하나 · 조용한 출석자 하나일 때의 추정)를 보고 모델이 사용자에게 묻는다.
    목소리 라벨("화자 A")은 그 회의 안에서만 한 사람이다 (`people.speaker_meeting_id`; 005가 기존에 공유되던 라벨을 회의마다 나눴다). 회의 카드에서 라벨의 이름을 정하면(`assign`) 그 회의의 발언 · 발언자 · 담당 · 참석자와
    회의록 글의 라벨이 그 사람으로 바뀌고, 그 사람이 출석할 때 목소리 학습에 동의했으면 speech-service가 그 목소리를 voiceprint로 익힌다(`transcripts.learn_voice`). 확정된 회의록은 정정으로만 바꾼다.
    담당이 없는 '각자' 할 일은 참석자마다 한 건으로 나눈다(`split`). `recall`은 이번 참석자들이 이전 회의에서 맡아 아직 열린 일과 같은 사람들이 있던 회의의 미결 쟁점이다 (저장 결과와 회의 카드). 이 회의의 주제(`meetings.subject`)로 열린 이전 회의가 있으면 그 주제 안에서만 찾는다.
  - **녹음 변환본 교정 (2026-11-03, `transcript_correction.py`, `library/tools/correct_transcript.py`).** 음성 인식은 용어 · 이름을 잘못 듣고("에젠드")
    한국어 띄어쓰기를 깨뜨린다. Group의 `/api/agents/meeting/run`은 Meeting Agent가 요청을 읽기 전에 `TRANSCRIPT_CORRECTION.prepare`를 돌린다:
    요청의 `[현재 요청]` 부분에 있는 변환본 줄(`[mm:ss] 화자: …`)만 ① 고정 치환(`data/transcript_glossary.json`의 replacements) ② 모델 교정
    (30줄씩 · 앞뒤 3줄 맥락 · 용어와 이름 목록 = 이 녹음의 출석자 + 임직원 디렉터리, 고친 줄만 JSON으로)으로 고치고, 변환본 뒤에 "[자동 교정] …"을 붙인다.
    문장을 다시 쓴 교정(원문 글자의 60% 미만이 남거나 길이가 30% 넘게 바뀜)은 버린다. 모델 호출이 실패하거나 답을 읽을 수 없는 구간은 치환만 남기고
    "[자동 교정 실패] …"로 알리며, Skill은 그때 `correct_transcript(transcript_id)`를 한 번 불러(speech-service에 보관된 원문을 다시 교정) 그 결과로 쓰게 한다.
    교정 단계는 `correct_transcript`의 tool_start / tool_end(`args.auto = true`)로 보고된다 (새 이벤트 타입 없음; gateway는 고친 줄 수 · 예 · 실패 구간을 단계에 표시).
    원래 변환본은 speech-service와 `transcript_segments`에 그대로 남는다. `TRANSCRIPT_CORRECTION=0`이면 자동 단계를 끈다 (Tool은 남음).
    Super Agent 위임 경로(`run_meeting_agent`의 delegation)에는 변환본이 오지 않으므로 이 단계가 없다.
  - **배경 작업.** `jobs.start_meeting_jobs`(app lifespan, `MEETINGS_BACKGROUND_JOBS=1`일 때만): 매분 발송 대기 메일을 SMTP(`SMTP_*`, 미설정이면 대기)로 발송(5회 실패하면 failed,
    발송 대기가 된 지 3일이 지난 메일은 늦게 보내지 않고 failed — 메일 서버를 나중에 설정했을 때 지난 안내가 한꺼번에 나가지 않도록),
    매일 `MEETINGS_DAILY_HOUR`(9시 KST)부터 기한 알림 · 주소 재조회 · 만료 transcript 삭제, system이 결정 감시를 주면 `MEETINGS_WATCH_INTERVAL`(600초)마다 새 기사 대조 (시작할 때 한 번 먼저). schema별 advisory lock.
  - **결정 감시 (2026-11-03, `decision_watch.py`, Tool `run_decision_review` · Skill `decision_review`, Group).** 회의록의 결정(결정 · 의결 · 기준)에 회의에서 밝힌 전제 · 조건을
    `save_meeting_minutes`의 `decisions[].premises`(kind premise | condition, `review_when`, `watch` 단어)로 남긴다. 배경 작업이 기사 store(`data/news.json`, 시작할 때 읽어 둠)의 새 기사를
    `watch` 단어로 거른 뒤 system의 chat model에게 전제마다 broken · at_risk · none을 묻고(전제 × 기사 한 번씩, 모델 오류면 at_risk · 단어 일치), 흔들린 전제는 상태를 바꾸고
    그 회의 참석자에게 `alert` 메일을 (결정 · 기사마다 한 번) 보낸다. `run_decision_review(query, topics, decision_ref)`는 질문 · 새 계획과 관련된 결정을 하나씩 점수 매겨
    근거 발언 · 전제 상태 · 들어온 신호를 주고, 계획의 금액(조 · 억)을 결정 금액과 코드로 비교해 넘으면 `conflicts`, 전제가 흔들렸으면 재검토 회의안을 붙인다. 이후 결정이 이전 결정을 대신하면(`supersedes`) 이전 결정의 신호는 더 세지 않는다.
  - **주제와 관제 (2026-11-03, `subjects.py`, `meeting_control.py`).** 회의마다 주제(`meetings.subject`, 예: 천안 신공장 · 뉴로엣지 인수)가 있다: 저장할 때 Agent가 준 이름, 이어지는 회의(carry over)의 주제,
    쓰고 있는 주제와 회의명 단어 일치, 아니면 회의명에서 '회의 · 검토 · 준비' 같은 말을 뺀 것 순서. 007 전에 저장된 회의는 관제를 처음 열 때 같은 규칙으로 정한다 (`fill_missing`, 오래된 회의부터).
    `meeting_control.control()`은 회사의 모든 회의를 한 화면으로: 주제마다 상태(alarm 이상 · warn 주의 · ok, 이유 · 지금 유효한 결정 · 다음 회의), 회의 목록(진행 중 · 기록 대기(지난 날짜에 예약된 채 회의록이 없음) · 예정 · 완료, 녹음 · 회의록 · 결정 · 분공 진행 · 메일),
    담당자별 할 일(같은 email · 이름은 한 사람, 소속은 `staff_directory`, 할 일마다 마지막 기록 — 메일 보고 · 독촉 등), 날짜별 활동(녹음 → 초안 → 확정 · 결정 변경 · 신호 · 메일 · 진행 보고)을 준다.
  - **업무 요청 (A2A).** `run_task_requests`(K): 다른 system의 A2A run은 `submit`만 할 수 있고(`task_requests` pending, 요청한 system = run의 `caller`),
    K의 확정 권한자가 `accept`(담당 · 기한 필수, 지정 회의 또는 따로 만든 접수 기록에 Action Item 생성 + 배정 메일) · `reject`한다. A2A `list`는 그 system이 보낸 요청과 연결된 Action Item 진행만 보여 준다.
- `backend/app/library/tools/run_company_risk_news_analysis.py`: `make_run_company_risk_news_analysis(path)` → `@tool("run_company_risk_news_analysis")`, 인자 `query: str, company: str, topics: list[str] | None`.
- `backend/app/library/workflows/company_risk_news_analysis.py`: 순수 함수 `analyze_company_risk_news(..., path=...)`가 주어진 기사 store(K: `data/news.json`, 가상 기사 5건)를 조회한다.
  `company`를 기사 `companies` alias와 대조해 대상 기업 기사만 남기고, `query`/`topics`에서 리스크 category 키워드(`customer_concentration`, `legal_dispute`, `key_personnel`, `labor_dispute`)를 찾아 좁힌 뒤 HIGH → MEDIUM → LOW, 최신순으로 정렬한다. 키워드가 없으면 그 기업의 모든 리스크를 돌려준다.
  반환은 dict (`matched`, `risks[]`에 `category, label, level, headline, summary, key_facts, source{id,date,name}`). 실제 뉴스 API / 검색 / RAG로 바꿀 때는 이 함수 본문만 교체하면 Tool · Agent · K Super 계약은 그대로다.
- `backend/app/library/tools/run_mna_review.py`: `make_run_mna_review(path)` → `@tool("run_mna_review")`, 인자 `query: str, company: str, persona: str = "CEO", context: str | None`.
- `backend/app/library/workflows/mna_review.py`: 순수 함수 `review_acquisition(..., path=...)`이 주어진 기업 store(K: `data/companies.json`, 가상 기업 1건: 재무·가치·고객·기술·전략)를 읽는다.
  - **persona는 Agent가 아니라 평가 관점 parameter**다. `PERSONAS` 표가 CEO(전략 적합성 · Time-to-Market · 리스크/기회 균형 · 사업 추진 가치),
    CFO(요구가격 vs 적정가치 · 수익성 · 고객 집중도 · 재무적 downside · 거래 조건), CTO(기술 적합성 · Time-to-Market · 핵심 기술인력 · IP 리스크 · 기술 통합)의 평가 기준을 **순서 있는 목록으로 명시**한다.
  - 13개 평가 항목은 모두 결정적 규칙(가격 프리미엄 임계값, 고객 비중 임계값 등)으로 평가된다. 모든 persona에 대해 전부 평가하되, 보고 순서 · 우려 목록 · 조건 순서는 persona 목록을 따르고 나머지는 `other_considerations`로 압축한다.
    권고(`PROCEED / CONDITIONAL_PROCEED / DO_NOT_PROCEED`)는 전체 항목으로 계산하므로 **같은 데이터면 persona가 달라도 권고와 조건 집합은 같고 근거·우선순위만 다르다**.
  - `context`는 호출자가 자연어로 넘기는 자유 텍스트다(고정 형식 없음). `extract_context_signals()`가 내부 가격 기준("2.5조 원 초과"), Time-to-Market 요구("2년 이상"), 외부 확인된 리스크 주제(고객 · 소송 · 핵심인력)를 찾아 해당 항목의 근거 문장에 반영한다. 판정 자체는 바꾸지 않는다.
  - 반환은 dict (`found, persona, persona_label, persona_focus, inputs, context_signals, assessments[], other_considerations[], positives[], concerns[], recommendation, recommendation_label, conditions[]`). 모르는 기업 · persona는 예외 대신 `found: false` / `error`로 돌려주어 모델이 정정할 수 있게 한다.
  - 실제 재무 모델 / 밸류에이션 서비스로 바꿀 때는 evaluator 함수들만 교체하면 Tool · Agent · K Super 계약은 그대로다.

### 3.4 Agent Delegation과 A2A의 차이

이 프로젝트에서 둘은 **같은 의미가 아니다**.

| 구분 | 의미 | 예 | frontend `CommunicationType` |
| --- | --- | --- | --- |
| **Internal Agent Delegation** | 같은 조직/시스템 내부에서 Super Agent가 자기 전문 Agent에게 업무를 맡기는 것 | K Super → Meeting / News / Advisory | `delegation` |
| **A2A** | 독립된 Agent System 또는 조직 경계를 넘는 통신 | Group Super ⇅ K Super | `a2a` |

- Specialist Agent 호출을 A2A라고 부르지 않는다.
- Delegation은 backend에서 `delegation_start` / `delegation_end` 이벤트로 드러난다. 일반 `tool_start`로 보고하지 않는다. UI 입장에서 위임은 Agent 간 handoff이기 때문이다.

**Current implementation**
- Delegation: K Super에 허용된 Tool `delegate_to_meeting_agent` / `delegate_to_news_agent` / `delegate_to_advisory_agent`(`systems/k_electronics/tools/`, 라이브러리 factory `library/tools/delegation.make_delegate_tool`로 생성)가 같은 프로세스 안에서 `run_meeting_agent(task)` / `run_news_agent(task)` / `run_advisory_agent(task)`를 호출한다.
- A2A (Phase 5a): Group system의 Tool `request_k_electronics_review`(`systems/group/tools/`)가 `runtime/a2a.request_review()`로 K의 `POST /api/a2a/run`을 HTTP 호출한다.
  `a2a_start(group_super → k_super, request)` emit → K의 NDJSON 스트림을 읽으며 K의 lifecycle 이벤트를 `relayed: true`로 자기 run에 다시 emit(관찰용) → 마지막 `agent_end`의 `response`를 꺼내 `a2a_end` emit 후 반환.
  Group Super의 `ToolMessage`에는 그 최종 텍스트만 들어간다. K의 `error`는 `AgentError("k_super", ...)`로 전파되어 Group run이 중단된다. 연결 실패도 같다.
  K의 주소는 `K_ELECTRONICS_A2A_URL`(기본 `http://localhost:8000/api/a2a/run`). 테스트는 `make_client`를 K app의 TestClient로 바꿔 프로세스 안에서 같은 경로를 돈다.
  요청자의 `principal`과 gateway 서명 `principal_token`을 그대로 넘긴다. 같은 Tool로 업무 전달("업무 요청: …")과 전달한 업무의 진행 조회도 한다 (K 미팅 에이전트의 `run_task_requests`).
  Group UI(`/group`)는 이 경로를 그대로 쓴다: `a2a_start` / relayed K 이벤트 / `a2a_end`가 채팅 옆 A2A 세션창이 된다 (Phase 7).

---

## 4. Capability Registry

### 개념

`Capability`는 Agent가 사용할 수 있는 능력의 상위 개념이고, 두 종류가 있다.

```text
Capability
├─ Skill   업무 지침 / playbook. Agent의 system context에 들어간다.
└─ Tool    실제 호출 가능한 실행 기능. LLM tool calling으로 노출된다.
```

Agent 파일마다 자기만의 Capability universe를 하드코딩하지 않는다.
시스템 전체에 하나의 **Capability Registry**가 있고, 각 Agent는 그중 **자신에게 허용된 subset**만 받아 쓴다.

```text
Registry
   ↓ AGENT_ID 기준 조회
Skills → compose_system_prompt(identity, skills)  → SystemMessage
Tools  → get_chat_model().bind_tools(tools)       → LLM tool calling
```

Registry가 답하는 질문: 어떤 Capability가 있는가 / Skill인가 Tool인가 / 어떤 Agent가 쓸 수 있는가 / 구현은 무엇인가.
Registry의 대상은 **Agent가 직접 사용할 수 있는 Capability**뿐이다. Tool 내부의 workflow 단계나 helper는 등록하지 않는다.

### Current implementation — `backend/app/runtime/capabilities.py` + `library/` + `systems/k_electronics/`

```text
runtime/capabilities.py   Skill(name, description, instructions) · Capability(impl, allowed_agents) · Registry(capabilities) — 조회·검증 메커니즘 (system 중립)
library/
  skills/                 Skill 본문 (specialist_delegation.py, meeting_context_analysis.py, external_risk_analysis.py, mna_feasibility_review.py)
  tools/                  Tool factory: make_run_meeting_context_retrieval(path) 등 — 이름 · 설명 · schema의 source of truth, 데이터는 path로 바인딩
  workflows/              Tool 내부 구현 workflow (순수 함수, LangChain 의존 없음, store 경로를 path로 받음). Agent-visible 아님
systems/k_electronics/
  registry.py             이 system의 CAPABILITIES 튜플 (권한의 source of truth) · REGISTRY = Registry(CAPABILITIES) · get_capability / capabilities_for / skills_for / tools_for
  tools/                  delegate_to_*.py (K Super의 위임 Tool) · run_*.py (라이브러리 factory를 data/에 바인딩한 instance)
  data/                   meetings.json · news.json · companies.json
```

| Capability | kind | allowed_agents | 구현 |
| --- | --- | --- | --- |
| `specialist_delegation` | skill | `k_super` | `library/skills/specialist_delegation.py` |
| `delegate_to_meeting_agent` | tool | `k_super` | `systems/k_electronics/tools/delegate_to_meeting_agent.py` → `agents/meeting.run_meeting_agent` |
| `delegate_to_news_agent` | tool | `k_super` | `systems/k_electronics/tools/delegate_to_news_agent.py` → `agents/news.run_news_agent` |
| `delegate_to_advisory_agent` | tool | `k_super` | `systems/k_electronics/tools/delegate_to_advisory_agent.py` → `agents/advisory.run_advisory_agent` |
| `meeting_context_analysis` | skill | `meeting` | `library/skills/meeting_context_analysis.py` |
| `meeting_minutes_writing` | skill | `meeting` | `library/skills/meeting_minutes_writing.py` |
| `meeting_follow_up` | skill | `meeting` | `library/skills/meeting_follow_up.py` |
| `run_meeting_context_retrieval` | tool | `meeting` | `systems/k_electronics/tools/run_meeting_context_retrieval.py` = `library/tools/...make_run_meeting_context_retrieval(MEETING_STORE)` → `library/workflows/meeting_context_retrieval.retrieve_meeting_context` |
| `save_meeting_minutes` | tool | `meeting` | `make_save_meeting_minutes(MEETING_STORE)` → `library/workflows/meeting_minutes.save_minutes` |
| `run_action_item_tracking` | tool | `meeting` | `make_run_action_item_tracking(MEETING_STORE)` → `library/workflows/action_items.track_action_items` |
| `update_action_item` | tool | `meeting` | `make_update_action_item(MEETING_STORE)` → `library/workflows/action_items.update_action_item` |
| `run_meeting_schedule` | tool | `meeting` | `make_run_meeting_schedule(MEETING_STORE)` → `library/workflows/meeting_schedule.meeting_schedule` |
| `correct_transcript` | tool | `meeting` (Group) | `make_transcript_correction(MEETING_STORE, data/transcript_glossary.json, agent="meeting").tool` → `library/workflows/transcript_correction.correct_lines` |
| `decision_review` | skill | `meeting` (Group) | `library/skills/decision_review.py` |
| `run_decision_review` | tool | `meeting` (Group) | `make_decision_watch(MEETING_STORE, data/news.json).tool` → `library/workflows/decision_watch.review_decisions` (같은 객체의 `scan()`이 배경 작업의 기사 대조) |
| `external_risk_analysis` | skill | `news` | `library/skills/external_risk_analysis.py` |
| `run_company_risk_news_analysis` | tool | `news` | `systems/k_electronics/tools/run_company_risk_news_analysis.py` = `make_run_company_risk_news_analysis(data/news.json)` → `library/workflows/company_risk_news_analysis.analyze_company_risk_news` |
| `mna_feasibility_review` | skill | `advisory` | `library/skills/mna_feasibility_review.py` |
| `run_mna_review` | tool | `advisory` | `systems/k_electronics/tools/run_mna_review.py` = `make_run_mna_review(data/companies.json)` → `library/workflows/mna_review.review_acquisition` |

Group system의 Registry(`systems/group/registry.py`)는 같은 메커니즘으로 자기 내용을 가진다 (Phase 5a·5b):
`group_super` ← Skill `subsidiary_review`(Group 전용) · `specialist_delegation`(라이브러리), Tool `request_k_electronics_review`(A2A client) · `delegate_to_{meeting,news,advisory}_agent`(Group 전용 설명, 라이브러리 factory `library/tools/delegation.make_delegate_tool`);
`meeting` / `news` / `advisory` ← 라이브러리 Skill 5개 + 라이브러리 Tool factory를 `systems/group/data/` · Group 회의 저장소(schema `grp`)에 바인딩한 instance 7개 (모두 19개).
어느 Tool을 쓸지(자기 Specialist 위임 vs K전자 A2A)는 Registry 제한 없이 요청과 Tool 설명에 따른 모델 판단이다.

설계 규칙:

- **Tool은 LangChain `BaseTool` 그 자체**다. 이름 · description · argument schema는 `@tool` 정의가 source of truth이고 Registry는 이를 중복 정의하지 않는다.
- **Skill은 `Skill` dataclass**(이름, 설명, 지침 텍스트)다. Agent 고유의 정체성 / 출력 형식 규칙은 Agent 파일의 `IDENTITY`에 남고, "이 업무를 어떻게 수행하는가"만 Skill이다.
- **권한(`allowed_agents`)은 system의 `registry.py`에만** 있다. Skill / Tool 모듈은 자기가 누구에게 허용되는지 모른다.
- **라이브러리는 system을 모른다.** Skill 본문과 Tool factory는 `library/`에 있고, system이 factory에 자기 데이터 경로를 넘겨 Tool instance를 만든 뒤 자기 Registry에 등록한다 (`tests/test_boundaries.py`).
- Agent 파일은 `skills_for(AGENT_ID)` / `tools_for(AGENT_ID)`만 호출한다 (`k_super.py`, `meeting.py`, `news.py`, `advisory.py`의 `SYSTEM_PROMPT`, `TOOLS`).
  Skill / Tool 모듈을 Agent 파일에서 직접 import하지 않는다.
- 위임 Tool은 대상 Agent의 `run_*` 함수를 **호출 시점에 import**한다. 대상 Agent도 Registry를 쓰므로 module-level import는 순환이 된다.
  이 덕분에 `k_super.py`는 `meeting.py` / `news.py` / `advisory.py`를 import하지 않는다.
- Registry는 import 시 이름 중복과 빈 `allowed_agents`를 거부한다. `tests/test_capabilities.py`가 Registry 내용과 Agent ↔ Registry 일치를 검증한다.

frontend에 있던 Workflow 이름 · 내부 단계 상수(`WORKFLOWS`, `MNA_REVIEW_STAGES`, `AGENTS[id].workflow`, `mockResults.ts`)는 Step 5가 real이 되면서 제거했다.
UI에 보이는 Workflow 이름은 모두 backend `tool_start` 이벤트의 실제 이름이다.

---

## 5. Agent State와 Context 경계

### LangGraph message-state 원칙

- Agent의 기본 state는 **message history**다. 현재 prototype은 `MessagesState`를 그대로 쓴다.
- Agent별 결과를 state field로 계속 늘리는 구조(`meeting_result`, `news_result`, `advisory_result`, ...)는 **지양**한다.
- 여러 Agent가 공유해야 하는 구조화 데이터가 분명히 필요해지면, agent-specific field 대신 범용 `artifacts` / `context` / `working memory` 같은 구조를 검토한다. 아직 도입하지 않았다.

### Agent 간 context 경계

각 Agent는 자기 message history를 가진 독립 Agent다.

```text
K Super MessagesState
  Human(A2A request)
  AI(tool_call: delegate_to_meeting_agent)
  Tool(Meeting Agent의 최종 응답 텍스트만)
  AI(tool_call: delegate_to_news_agent)      ← Meeting 결과를 보고 다시 판단
  Tool(News Agent의 최종 응답 텍스트만)
  AI(tool_call: delegate_to_advisory_agent)  ← 두 결과를 보고, 필요한 맥락을 task 문장에 직접 써서 위임
  Tool(Advisory Agent의 최종 응답 텍스트만)

Meeting Agent MessagesState   ← 별도 그래프, 별도 history (News / Advisory Agent도 동일)
  Human(task)
  AI(tool_call: run_meeting_context_retrieval)
  Tool(조회 결과 JSON)
  AI(최종 응답)
```

- 전문 Agent 내부의 LLM 메시지 / Tool Call / Tool Result는 K Super history에 **병합하지 않는다**.
- 상위 Agent에는 필요한 결과(최종 응답)만 `ToolMessage`로 전달한다.

**Current implementation**: 위 그대로다. `delegate_to_*` Tool은 `run_*_agent()`의 반환 문자열만 돌려주고, K Super는 그것을 `ToolMessage`로 받는다.
Tool 결과의 구조화 데이터(dict)는 이벤트(`tool_end.result`)로 UI에 전달될 뿐, 상위 Agent state에는 들어가지 않는다.
Advisory가 필요로 하는 Meeting / News 결과도 별도 공유 구조 없이 K Super가 쓴 위임 `task` 문장으로만 전달되고, Advisory Agent는 그 문장을 `run_mna_review`의 `context` 인자로 넘긴다.

---

## 6. Backend와 Frontend의 책임

### 원칙

**Backend가 Agent 실행의 source of truth다. Frontend는 Agent가 실제로 무엇을 실행했는지를 시각화한다.**

```text
Backend                       Frontend
  Agent / Tool 실행               실행 상태 표시
  실제 lifecycle 발생             실제 tool name 표시
  실제 request / task             실제 request / task 표시
  실제 tool arguments / result    실제 arguments / result 표시
  실제 agent response             실제 response 표시
```

Frontend가 "Meeting Agent니까 이 Workflow를 실행했겠지"라고 추측하거나, 실행되지 않은 workflow를 하드코딩해 보여주는 구조는 지향하지 않는다.
Transport(NDJSON, SSE, WebSocket 등)는 implementation detail이다. 핵심은 **실제 backend lifecycle이 frontend 상태의 source of truth**라는 것이다.

### Current implementation — backend lifecycle 이벤트

`backend/app/runtime/events.py`

- Agent 코드는 실제 lifecycle 시점에 `emit(event)`를 호출한다. emitter는 `contextvars`로 주입되어 실행 밖에서는 no-op이다.
- `stream_run(fn, run=RunContext)`은 Agent를 worker thread에서 돌리고 이벤트를 발생 즉시 yield한다. 마지막은 `done` 또는 `error`.
  `RunContext(run_id, system, root_agent, origin, trace_id)`는 `new_run()`으로 만들고, run 안에서 emit된 모든 이벤트에 `run_id` · `system` · `origin` · (`trace_id`)가 찍힌다.
  이미 찍혀 있는 필드는 유지한다(다른 system에서 relay된 이벤트). `AgentError`가 아닌 예외는 `root_agent`로 귀속한다.
- **관찰 채널 (Phase 3).** `stream_run`은 요청자 큐뿐 아니라 프로세스 전역 `HUB`(`EventHub`)에도 모든 이벤트를 publish한다. `runtime/api.py`의 `sse_events()`가 그것을 `text/event-stream`으로 흘리며(15초 keep-alive), 각 system의 app이 `GET /api/events`로 연다.
  요청을 보낸 쪽이 누구든(사용자 직접 채팅, 다른 system의 A2A 요청) 이 프로세스에서 일어나는 run은 전부 보인다. 늦게 구독한 관찰자에게 과거 이벤트를 replay하지는 않는다.
- `collect_events(fn)`은 테스트용 동기 수집기다.
- `AgentError(agent, message)`로 실패한 Agent를 지목한다. 지목되지 않은 예외는 run의 `root_agent`(K: `k_super`, Group: `group_super`, 직접 대화는 그 Agent)로 귀속된다.

| 이벤트 | payload | 발생 위치 |
| --- | --- | --- |
| `agent_start` | `agent, request` | `agent_core.run_super`(K Super / Group Super), `agent_core.run_specialist`(Meeting / News / Advisory) 시작 |
| `agent_token` | `agent, text` | `agent_core.invoke_model`: 모델이 텍스트를 쓰는 동안 조각마다 (tool call 인자는 텍스트가 아니므로 흐르지 않음). 조각을 합친 메시지가 그래프에 들어가므로 판단 로직은 `invoke`와 같다 |
| `a2a_start` | `from, to, request` | Group의 A2A client Tool 진입 (Phase 5a) |
| `a2a_end` | `from, to, response` | 위 Tool 반환 직전. 그 사이의 K 이벤트는 `relayed: true`로 relay된다 |
| `delegation_start` | `from, to, task` | `delegate_to_*` 진입 |
| `delegation_skipped` | `from, to, reason` | `delegate_to_*` 진입 시 `principal.agents`에 `to`가 없으면 (`reason: permission`). 전문 Agent는 실행되지 않고 Super Agent는 나머지로 답한다 |
| `tool_start` | `agent, tool, args` | `agent_core.execute_tool_calls` (Specialist의 Workflow Tool) |
| `tool_end` | `agent, tool, result` | 위 Tool 실행 직후 |
| `agent_end` | `agent, response` | 각 Agent 정상 종료 |
| `delegation_end` | `from, to, response` | 위임 Tool 반환 직전 |
| `error` | `agent, message` | `stream_run`이 예외를 변환 |
| `done` | – | 정상 종료 |

K Super 실행 한 번(Step 3·4·5·6)의 현재 scenario에서의 실제 이벤트 순서 (`tests/test_agents.py`가 검증). 위임 횟수와 대상은 요청에 따라 달라진다:

```text
agent_start(k_super)
→ delegation_start(k_super→meeting)  → agent_start(meeting)  → tool_start → tool_end → agent_end(meeting)  → delegation_end
→ delegation_start(k_super→news)     → agent_start(news)     → tool_start → tool_end → agent_end(news)     → delegation_end
→ delegation_start(k_super→advisory) → agent_start(advisory) → tool_start → tool_end → agent_end(advisory) → delegation_end
→ agent_end(k_super, response=최종 검토 결과)   ← Step 6
→ done
```

Step 번호는 backend에 없다. 이벤트 종류도 Step 3과 같다 (새 이벤트 타입 없음). Step 6의 신호는 K Super의 `agent_end`이며 `response`가 비어 있지 않다.

### Current implementation — API

`backend/app/systems/k_electronics/app.py` (FastAPI; `backend/app/main.py`가 `uvicorn app.main:app`용으로 재노출)

| Endpoint | 역할 |
| --- | --- |
| `GET /api/health` | 상태 확인 (`system` 포함) |
| `GET /api/events` | 관찰 채널. 이 프로세스의 모든 run 이벤트를 SSE로 스트리밍 (Demo Monitor · 플랫폼 UI의 외부 실행 표시용) |
| `POST /api/a2a/run` `{request, trace_id?, caller?, principal?}` | **A2A 수신 표면.** 다른 system의 Super Agent가 보낸 요청으로 K Super 실행(`invocation="a2a"`, 필요한 전문 Agent에게 순차 위임 후 최종 검토 결과 작성). lifecycle을 NDJSON으로 스트리밍하고 `agent_end(k_super).response`가 A2A 응답이다. `trace_id` · `caller`는 이벤트 stamp로만 쓰인다 (업무 payload 아님). `principal`은 상대 system이 대신 요청하는 사용자다: K는 자기 자료에 그 사용자의 등급을 적용하고, 이 run에서는 회의 기록을 바꾸지 않는다 |
| `POST /api/agents/k-super/run` `{request}` | 사용자 → K Super 직접 대화 (`invocation="direct"`, 위임 없이 답해도 된다). 같은 Agent core |

`backend/app/systems/group/app.py` (FastAPI, `uvicorn app.systems.group.app:app --port 8001`)

| Endpoint | 역할 |
| --- | --- |
| `GET /api/health` | 상태 확인 (`system: group`) |
| `GET /api/events` | 관찰 채널 (relay된 K 이벤트 포함) |
| `POST /api/agents/group-super/run` `{request}` | 사용자 → Group Super 직접 대화. 자기 Specialist에게 위임하거나 A2A Tool로 K에 요청하고 그룹 판단을 쓴다. NDJSON 스트림에 `delegation_*`(Group) · `a2a_start` · relayed K 이벤트 · `a2a_end` · `agent_end(group_super)`가 흐른다 |
| `POST /api/agents/{meeting,news,advisory}/run` `{request}` | 사용자 → Group의 전문 Agent 직접 대화 (`invocation="direct"`, Group 데이터). 같은 Agent core |

K system에도 같은 모양의 `POST /api/agents/{meeting,news,advisory}/run`이 있다 (K 데이터).

Group system에는 Agent를 거치지 않는 **회의 업무 화면용** `POST /api/desk/{overview,mail,flags,items,meeting,schedule,report,minutes,checkin,attendance,watch,control,finalize}`도 있다 (2026-10-30, `library/workflows/meeting_desk`;
`watch`는 전제가 흔들린 결정, `control`은 관제(`meeting_control`), `finalize`는 저장된 초안을 Agent 없이 확정(메일 발송)한다 — 2026-11-03;
`checkin` open · status · close · join과 `attendance` assign · add · remove, `items`의 `action: split`은 2026-11-03, `library/workflows/attendance`). `meeting`은 `attendance` · `recall`도 준다.
요청자(principal) 대신 메일 버튼의 서명된 token으로 담당자를 정하는 `POST /api/mail-action {token, status?, note?}`도 있다 (2026-11-04, `library/workflows/mail_actions`, gateway `/meetings/act/<token>`).
`report` `{meeting_id, kind: minutes|report}`는 저장된 회의록으로 만든 **회의록(양식) · 회의 결과 보고서(개조식) PDF**를 돌려준다 (`library/workflows/meeting_report`, fpdf2 + 나눔 글꼴;
양식은 data.go.kr에 공개된 공공데이터전략위원회 회의록 · 회의 결과를 따름). 같은 PDF가 회의록 메일에 첨부되고, 의장 · 간사 메일에는 결과 보고서도 붙는다.
배포에서는 이 desk API를 Open WebUI 채팅 안의 회의 카드가 부른다 (gateway가 회의 Tool 결과로 답변 끝에 카드 블록을 붙이고 Open WebUI 스크립트가 그림). 회의록 저장 Tool은 회의 시각(start_time · end_time)도 받는다.
사람이 회의 기록과 Agent가 보낸 메일을 목록으로 보고 Compliance 검토 · Action Item 완료 · 실패한 메일 다시 보내기를 직접 누르는 경로다. body는 run과 같은 `principal` / `principal_token`(+ action · mail_ids · reviews · item_id …)이고,
미팅 Tool과 같은 workflow 함수 · 권한 규칙(VISIBLE · can_confirm · can_write)을 쓴다. 배포에서는 gateway의 `/meetings/` 화면이 Open WebUI 로그인 사용자로 이것을 부른다.

모든 run 요청 body는 선택 필드 `principal {email, name, groups, clearance, agents}`와 `principal_token`을 받는다 (`runtime/schemas`). gateway가 Keycloak 로그인 사용자로 채우고,
`agents`는 그 사용자가 쓸 수 있는 전문 Agent id(gateway 권한표; 없으면 제한 없음)로, 위임 Tool이 그 밖의 Agent에는 위임하지 않는다 (11장 20번).
`PRINCIPAL_SIGNING_KEY`(gateway와 backend가 공유)로 HS256 서명한 `principal_token`을 함께 보낸다. `runtime/api.run_identity`가 `runtime/principal.trusted_principal`로 확인한다:
키가 있으면 서명이 맞는 토큰의 사용자만 쓰고, 토큰 없이 사용자를 보내거나 서명 · 만료가 틀리면 **401**, 둘 다 없으면 익명(`MEETINGS_ANONYMOUS_CLEARANCE`). 키가 없으면(로컬 UI · 테스트) `principal`을 그대로 쓴다.
`new_run(..., principal=, principal_token=)`이 `RunContext`에 담는다. 이벤트에는 찍히지 않으며 Agent(LLM)에게도 보이지 않는다. Tool이 `current_run()`으로 읽는다.
Group의 A2A Tool은 자기 run의 principal과 토큰을 K의 `/api/a2a/run`에 그대로 넘긴다 (`runtime/a2a.request_review(..., principal=, principal_token=)`) — K도 같은 키로 검증한다.

### Current implementation — frontend 반영

- `src/live/api.ts` — NDJSON 스트림 클라이언트. `error` 이벤트는 `LiveRunError(agent, message)`로 던진다.
- `src/live/agentEvents.ts` — backend 이벤트 → 기존 `ScenarioEvent`로 변환. 예약·추측 없이 도착한 이벤트만 상태로 바꾼다.
  Workflow 결과 렌더링(`toolResultBlocks`)은 Tool 이름별 `switch`(meeting, news, advisory)이고, 모르는 Tool은 JSON으로 표시한다.
  `run_mna_review`는 persona 배지 · 입력 정보 · persona 우선순위대로의 평가 항목 · 권고 배지 · 조건 목록으로 그린다. 값은 전부 backend 결과이며 frontend가 만들지 않는다.
  한 K Super 실행에 위임이 여러 번 있어도 같은 변환이 반복된다. `delegation_end`의 `session_close`에는 UI 전용 `delay`가 있어 다음 세션 창이 열리기 전에 이전 창이 접힌다.
- `src/state/runState.ts` — reducer가 `nodes / edges / messages / sessions` 상태를 만들고, `createEventQueue`가 도착하는 이벤트를 **도착 순서대로 즉시 적용**한다. 이벤트의 `delay`는 상태 변경을 늦추지 않고 다음 이벤트만 보류한다. 제품 UI(탭마다 하나)와 Demo Monitor가 같은 것을 쓴다.
- 실패 시 backend가 지목한 Agent를 error로 표시하고 실행을 **중단**하며 열린 세션을 닫는다. mock으로 대체하지 않는다.

| backend 이벤트 | UI |
| --- | --- |
| `agent_start`(super) | 노드 status "요청 분석 중..." |
| `delegation_start` | delegation 세션 열림(실제 위임 문장) · 대상 노드 active · edge active |
| `agent_token` | 세션(또는 전사)에서 그 Agent의 응답 초안이 제목 없이 제자리에서 자란다(`message_delta`, 깜빡이는 caret). `agent_end`의 최종 메시지(제목 있음)가 초안을 대체하고, tool call · 위임 · A2A로 넘어가면 초안은 제목 없는 진행 메모로 확정된다(`message_commit`) |
| `tool_start` | 노드/세션에 실제 Workflow 이름, status "실행 중" |
| `tool_end` | 세션에 Workflow 인자·결과 메시지 |
| `agent_end`(specialist) | 세션에 최종 응답, 노드 completed, edge completed |
| `delegation_end` | 세션 닫힘, 위임자 status "결과 수신" |
| `agent_end`(super) | 열린 세션(또는 전사)에 그 Super의 최종 응답 메시지(실제 `response` 문자열 그대로). 노드 completed는 `a2a_end`(상대 Super) 또는 run의 `done`(root)이 담당 |
| `a2a_start` / `a2a_end` | A2A 세션 열림(실제 요청문) · 상대 Super 노드 active / 세션 닫힘(result = 실제 응답) · 상대 노드 completed |
| `error` | 해당 노드 error, 오류 메시지, 열린 세션 닫힘, `error` 상태 |

모든 탭이 직접 대화다(`platform/usePlatformChats.ts`). mock 응답 경로는 없다. 직접 대화에서 Tool 없이 답하면 `tool_start`가 없으므로 Workflow 이름 없이 응답 메시지만 붙는다.

### Current implementation — 제품 UI (Phase 7, `/group` · `/k`)

`src/platform/`:
- `config.ts` — 플랫폼마다 다른 것만: 제목, 탭 4개(id · 라벨 · backend 경로 · 헤더 · 예시 요청), `resolveAgent`. `GROUP_PLATFORM`은 자기 Specialist를 `group_*` id로 보여 relay된 K Specialist(`meeting` 등)와 구분한다. `K_PLATFORM`은 backend id 그대로.
- `usePlatformChats.ts` — 탭마다 전사 · 세션 상태(`RunnerState`)를 유지한다. `send(tab, text)`는 그 탭의 backend 엔드포인트에 NDJSON 요청을 보내고, 도착하는 이벤트를 같은 mapper · reducer로 적용한다(사용자 메시지 추가 → root `agent_start` → 이벤트 → `agent_complete`, 실패 시 backend가 지목한 Agent로 `scenario_error`). 탭을 바꿔도 각 전사가 남는다.
- `PlatformApp.tsx` — Header · Sidebar(설정의 탭) · ChatPanel · `SessionWindows`. 그래프 · Step · toolbar 없음. A2A / 위임 세션창은 채팅 옆에 열렸다가 요청 카드로 접힌다 (제품 기능).
- `reducer`의 `scenario_error`는 열려 있던 세션을 모두 닫는다 (다음 요청이 옛 세션으로 라우팅되지 않도록).
- `useInboundRuns.ts` — A2A 수신 표면이 있는 플랫폼(K)에서 자기 backend의 관찰 채널을 구독해 `origin: a2a`(relay 아님) run을 Super 탭에 보여 준다: root `agent_start`에 "외부 A2A 요청 · 그룹" A2A 세션을 열고(요청문), 이후 이벤트를 같은 mapper로 적용(위임 창 중첩, K Super 응답 스트리밍), `done`에 "응답 반환"으로 닫는다. Sidebar 배지는 큐가 비워진 뒤 꺼진다.
- `src/main.tsx`: `/group`(기본) · `/k` · `/monitor`.
- 검증(headless): `/group`에서 예시 요청 → A2A 세션창 안에 K의 위임 창이 차례로 열리고 닫힘 → "그룹 최종 판단" 메시지, 같은 시간 `/monitor`는 8/8 완료. `/k`에서 뉴스 직접 채팅(Tool 결과 + 응답)과 K Super 직접 채팅(미팅 위임 창 → "K전자 검토 결과").

### Current implementation — Demo Monitor (Phase 6, `/monitor`)

테스트 · 발표용 관찰 화면이며 제품 UI의 navigation에 없다. `src/main.tsx`가 URL 경로로 화면을 고른다 (`/monitor` → `DemoMonitor`, `/k` → K UI, 그 외 → Group UI). 라우터 의존성은 없다.

- `src/live/api.ts` `subscribeAgentEvents(url)`: backend `GET /api/events`(SSE)를 브라우저 `EventSource`로 구독한다. Vite dev proxy는 `/api` → :8000(K), `/group-api` → :8001(Group, `/api`로 rewrite).
- `src/hooks/useDemoObserver.ts`: Group backend의 관찰 채널 하나를 구독한다. relay 덕분에 K의 lifecycle도 같은 채널에 흐른다.
  그 system에서 시작된 run(relay되지 않은 첫 `agent_start`)이 오면 새 화면을 시작하고(요청문을 사용자 메시지로), 그 run과 `trace_id`가 같은 relayed 이벤트를 포함해 모든 이벤트를 도착 순서대로 같은 mapper · reducer에 적용한다.
  `done`이면 root Agent completed + `scenario_complete`, `error`면 backend가 지목한 Agent로 `scenario_error`(relay된 K의 오류가 먼저 오면 그것을 쓴다). 아무것도 시작하지 않는다.
- `src/monitor/steps.ts`: Step 1~8 해석(11장 8번 표). backend에는 Step이 없다.
- `src/live/agentEvents.ts`의 mapper는 `a2a_start`(A2A 세션 열림 · 상대 Super 노드 active · A2A edge) / `a2a_end`(세션 닫힘, result = 상대의 최종 응답, 상대 노드 completed)를 알고,
  `resolveAgent(system, agent)` 옵션으로 backend id를 화면의 `AgentId`로 바꾼다. 전체 그래프에서는 Group system의 `meeting` / `news` / `advisory`가 `group_meeting` / `group_news` / `group_advisory`가 된다 (`resolveForFullGraph`).
- `src/types/agent.ts`: `AgentId`에 `group_meeting` · `group_news` · `group_advisory` 추가. 그래프는 `GraphUniverse`(노드 · 좌표 · pool)를 받으며 `DEFAULT_UNIVERSE`(6노드, `AgentGraph`의 기본값)와 `FULL_UNIVERSE`(9노드 · pool 둘, Monitor가 사용)가 있다.
- 검증: headless Chromium으로 `/monitor`를 열고 curl로 Group에 요청을 보내면 Step 1 → 8이 실제 이벤트로만 전진하고, Group 회장 · Group Super · K Super · K Specialist 3개가 completed, 호출되지 않은 Group Specialist는 idle로 남는다.

---

## 7. 개발 전략: vertical slice

전체를 한 번에 다시 쓰지 않는다. 처음에는 deterministic 8-step demo의 mock Step을 **한 Step씩 실제 실행으로 교체**했고(Step 3 → 4 → 5 → 6),
2026-10-12에는 그 위에서 **두 Agent System으로의 재편을 Phase 단위**로 진행했다 ([11장](#11-목표-구조와-전환-계획-2026-10-12-결정)).
각 단계는 그 시점의 데모가 그대로 동작하는 상태로 끝나야 하며, 동작 변화 없는 정비(Phase 1·2)와 기능 추가를 섞지 않는다.

- 실패 시 mock fallback은 하지 않는다. backend가 꺼져 있거나 Agent가 실패하면 실행은 오류 상태로 멈춘다.
- mock scenario 파일(`scenarioEvents.ts` 등)과 옛 단일 앱은 Phase 8에서 제거했다. 남아 있는 고정 문장은 예시 요청(`platform/config.ts`)뿐이다.

---

## 8. 데모 scenario와 구현 상태

데모 scenario: **NeuroEdge(뉴로엣지) 인수 검토**. 고정된 scenario 파일은 없다. Group UI의 그룹 슈퍼에이전트 탭에 요청(예시 요청 버튼)을 보내면 Group Super가 실제로 판단해 실행한다.
아래 Step은 Demo Monitor가 실제 이벤트에서 해석하는 표시이며, 어떤 Step이 어떤 순서로 나오는지는 Agent들의 실제 행동이 정한다.

| Step | 내용 | 상태 | 실행 주체 |
| --- | --- | --- | --- |
| 1 | 회장 → Group Super. 인수 검토 요청 | **REAL** | Group UI(`/group`) → `POST /group-api/agents/group-super/run` (Group backend :8001). Group Super `agent_start` |
| 2 | Group Super → K Super. A2A 인수안 검토 요청 | **REAL** | Group Super의 Tool `request_k_electronics_review` → K `POST /api/a2a/run` (HTTP). 요청문은 Group Super가 쓴다. `a2a_start` → A2A 세션 |
| 3 | K Super → Meeting Agent → `run_meeting_context_retrieval` | **REAL** | K backend :8000. K 이벤트는 Group 스트림에 relay되어 A2A 세션 안에 위임 세션으로 보인다 |
| 4 | K Super → News Agent → `run_company_risk_news_analysis` | **REAL** | 같은 K Super 실행 안에서 Meeting 결과 수신 후 순차 위임 |
| 5 | K Super → Advisory Agent → `run_mna_review` | **REAL** | 같은 K Super 실행 안에서 Meeting · News 결과를 보고 맥락을 담아 순차 위임 |
| 6 | K Super가 전문 Agent 결과 종합 → K전자 차원의 최종 검토 결과 | **REAL** | 같은 K Super 실행의 마지막 `model` 턴. Tool 호출 없이 응답 → `agent_end(k_super)` |
| 7 | K Super → Group Super. A2A 응답 | **REAL** | K의 최종 응답 텍스트만 Group Tool이 반환 → `a2a_end` → A2A 세션 닫힘(result = 실제 응답) |
| 8 | Group Super → 회장. 그룹 투자심의 최종 판단 | **REAL** | Group Super의 마지막 `model` 턴 → `agent_end(group_super)`. Group Super가 자기 Specialist에게도 위임할 수 있다(모델 판단) |

Step 번호는 Demo Monitor(`/monitor`)만 표시한다. 제품 UI에는 Step도 그래프도 없다.

직접 대화 탭 (모두 REAL, 플랫폼별):

| 플랫폼 UI | 탭 | backend |
| --- | --- | --- |
| Group (`/group`) | 그룹 슈퍼에이전트 · 경영자문 · 뉴스 · 미팅 | `/group-api/agents/{group-super,advisory,news,meeting}/run` (:8001, Group 데이터) |
| K Electronics (`/k`) | K전자 슈퍼에이전트 · 경영자문 · 뉴스 · 미팅 | `/api/agents/{k-super,advisory,news,meeting}/run` (:8000, K 데이터) |

Super 탭도 직접 대화다(`invocation="direct"`). 인사에는 Tool 없이 답하고, 검토 요청에는 위임 · A2A 세션이 채팅 옆 창으로 열린다.

---

## 9. Step 3·4·5·6의 현재 architecture

의도:

```text
K Electronics Super
    ↓ LLM 판단 (tool calling)
delegate_to_meeting_agent(task)
    ↓
Meeting Agent
    ↓ LLM 판단 (tool calling)
run_meeting_context_retrieval(query, topics)
    ↓
meetings.json
    ↓
Meeting Agent 최종 응답 (한국어)
    ↓ ToolMessage
K Super
    ↓ LLM 판단 (Meeting 결과를 보고 다음 위임 결정)
delegate_to_news_agent(task)
    ↓
News Agent
    ↓ LLM 판단 (tool calling)
run_company_risk_news_analysis(query, company, topics)
    ↓
news.json
    ↓
News Agent 최종 응답 (한국어)
    ↓ ToolMessage
K Super
    ↓ LLM 판단 (Meeting · News 결과 중 Advisory에 필요한 맥락을 task 문장에 직접 씀)
delegate_to_advisory_agent(task)
    ↓
Advisory Agent
    ↓ LLM 판단 (persona 선택, task의 맥락을 context로 전달)
run_mna_review(query, company, persona, context)
    ↓
companies.json + persona 평가 기준 + context 신호
    ↓
Advisory Agent 최종 응답 (한국어)
    ↓ ToolMessage
K Super
    ↓ LLM 판단 (요청의 항목에 대한 결과가 모두 있음 → 추가 위임 없음)
최종 검토 결과 (Tool 호출 없는 AI 응답, 한국어) → END, agent_end(k_super)
```

위 흐름에서 위임 대상과 횟수는 요청이 정한다. 코드에는 "Meeting → News → Advisory" 순서도, "세 번 위임 후 종료"도 없다.

코드 대응:

| 단계 | 코드 |
| --- | --- |
| A2A 요청 도착 | Group Super의 Tool `request_k_electronics_review` → `runtime/a2a.request_review()` → K `POST /api/a2a/run` |
| API | `systems/k_electronics/app.py` `a2a_run` → `ndjson_run(lambda: run_k_super(request, invocation="a2a"), new_run(system="k_electronics", root_agent="k_super", origin="a2a", trace_id, caller))` |
| K Super LLM 판단 | `agent_core.build_super_graph`의 `call_model`: `SystemMessage(SPEC.system_prompt + 호출 맥락)` + `bind_tools(SPEC.tools)`. 둘 다 `k_super.py`가 Registry에서 `skills_for` / `tools_for("k_super")`로 받은 것 |
| 위임 | `agent_core.build_super_graph`의 `run_tools` → `systems/k_electronics/tools/delegate_to_*.invoke` → `delegation_start` emit → `run_*_agent(task)` |
| 전문 Agent LLM 판단 | `agent_core.build_specialist_graph`의 `call_model` (각 Agent 모듈이 Registry에서 받은 `SYSTEM_PROMPT`, `TOOLS` + 호출 맥락 문단) |
| Workflow Tool 실행 | `agent_core.execute_tool_calls` → `tool_start` emit → `systems/k_electronics/tools/run_*`(library factory instance).invoke → `library/workflows/*` → `tool_end` emit |
| 응답 | `agent_core.run_specialist`: delegation이면 Tool 호출 검사 → `final_response` → `agent_end` emit → 반환 → `delegation_end` emit → `ToolMessage` |
| 다음 위임 / 종료 | `agent_core.build_super_graph`: `tools → model` 고정 엣지. 다음 `model` 턴이 Tool을 호출하면 `route_on_tool_calls` → `tools`, 아니면 → `END` |
| 종합 (Step 6) | 마지막 `model` 턴의 AI 응답. `agent_core.run_super`가 근거 검사(`a2a`: 위임 1회 이상, 응답 비어 있지 않음) 후 `agent_end(k_super, response)` emit |
| UI 반영 | K 이벤트가 Group Tool에 의해 relay → Group UI: `agentEvents.ts` mapper → `state/runState.ts` reducer(A2A 세션 안의 위임 세션). Demo Monitor: 같은 mapper · reducer + `monitor/steps.ts`(Step 3 / 4 / 5 / 6) |

LLM: `backend/app/runtime/llm.py` — 기본은 `ChatOllama`, Ollama Cloud(`https://ollama.com`), 기본 모델 `deepseek-v4.1-flash`, `OLLAMA_API_KEY` 필요. `LLM_PROVIDER=openai`이면 `ChatOpenAI`로 OpenAI 호환 API(`OPENAI_API_KEY` · `OPENAI_MODEL` · `OPENAI_BASE_URL`, 예: Groq의 `openai/gpt-oss-120b`)를 쓴다. 키는 `backend/.env`에 두고 Git에 넣지 않는다.

테스트:

| 파일 | LLM 필요 | 검증 내용 |
| --- | --- | --- |
| `backend/tests/test_workflow.py` | 아니오 (테스트 DB) | 회의 조회 workflow — 회의 저장소 seed 위에서 기존 검색 결과 그대로, 등급 필터(대외비 회의는 사내 사용자에게 읽히지 않음), id · 기간 조회 |
| `backend/tests/test_meeting_records.py` | 아니오 (테스트 DB) | 회의록 초안 → 확정(담당 · 기한 없으면 확정 안 됨) → 정정(사유 필수, snapshot, Action Item 유지), 쓰기 권한(등급 · A2A), 진행 조회(지연 순 · 건수 · 담당 · 기간 · 키워드), 갱신 이력과 후보, 일정 예약 · 이월 · 취소, viewer가 run의 principal에서 오는지, system별 저장소 분리, 저장소 없음 → 오류 결과 |
| `backend/tests/test_news_workflow.py` | 아니오 | 뉴스 리스크 workflow (기업 alias, category 필터, 정렬) |
| `backend/tests/test_mna_review_workflow.py` | 아니오 | M&A 검토 workflow (persona별 항목 순서 · 우려 · 조건 순서, 같은 권고와 조건 집합, context 신호, 모르는 기업/persona) |
| `backend/tests/test_capabilities.py` | 아니오 | Registry 내용(kind, allowed_agents), Tool 정의가 source of truth, Agent가 Registry subset을 쓰는지, Skill이 system prompt에 들어가는지 |
| `backend/tests/test_k_super.py` | 아니오 | `route_on_tool_calls`(tool call → tools, 일반 응답 → END), `tools → model` 고정 엣지, scripted model(`agent_core.get_chat_model` patch) + stub 위임 Tool로 만든 그래프(`k_super.build_k_super` patch)로 `run_k_super`: 일부 전문 Agent만 쓰고 답해도 정상 종료 · 위임 없는 응답과 빈 응답은 `AgentError` |
| `backend/tests/test_events.py` | 아니오 | emit / collect / stream / error 귀속(root_agent) / run 식별자 stamp(relay된 stamp 유지) / HUB publish·subscribe·keep-alive / SSE 형식 / `.env` 경로 |
| `backend/tests/test_k_app.py` | 아니오 | K app 표면을 scripted model로 end-to-end: `/api/a2a/run`이 K Super → Meeting → Tool 체인을 돌리고 trace_id · caller · origin을 stamp, 위임 없는 답은 `error`, `/api/agents/k-super/run`은 위임 없이 답해도 정상 |
| `backend/tests/test_group.py` | 아니오 | Group Registry 내용(12개), Agent가 Registry subset을 쓰는지, 위임 Tool의 from/to, Group Tool instance가 Group 데이터만 읽는지(K와 분리), A2A Tool이 K lifecycle을 relay하고 최종 응답만 반환, K 실패·연결 실패를 `k_super`로 귀속, Group app → K app 프로세스 내 end-to-end, Group Super → 자기 Specialist 위임, Specialist 직접 endpoint |
| `backend/tests/test_group_workflows.py` | 아니오 (회의는 테스트 DB) | 라이브러리 workflow 3개를 Group 데이터로 실행 |
| `backend/tests/test_boundaries.py` | 아니오 | import 경계: runtime은 library·systems를, library는 systems를, system은 다른 system을 import하지 않음. system마다 registry.py와 data/ 존재 |
| `backend/tests/conftest.py` | – | 모든 테스트에서 `MEETINGS_DATABASE_URL`을 지운다. `meeting_db` fixture: `MEETINGS_TEST_DATABASE_URL`(이름에 test가 든 버릴 DB)의 `kelec` · `grp` schema를 지우고 다시 seed. 변수가 없으면 해당 테스트 skip |
| `backend/tests/test_agents.py` | 예 (`OLLAMA_API_KEY` 없으면 skip, 테스트 DB 필요) | Meeting / News / Advisory Agent 단독 실행, K Super 직접 대화(인사에는 위임 없이 · 회의 기준 요청에는 Meeting 위임), Group Super → K A2A 실제 왕복(relay된 K 위임 세 건, 그룹 판단; Group 자체 위임은 허용) · Group 직접 인사 · Group 미팅 에이전트 직접 대화(Group 데이터), 미팅 에이전트가 transcript로 회의록 초안을 저장(확정하지 않음) · 지연 Action Item 조회, K Super가 NeuroEdge 요청에 Meeting → News → Advisory를 각각 별도 model 턴에서 순차 위임하고 Advisory task에 회의 기준·외부 리스크를 담았는지(`context_signals`), 마지막에 Tool 호출 없는 최종 응답(본실사 판단 포함)으로 끝나는지, 회의 결정사항만 묻는 요청에는 Meeting에게만 위임하고 답하는지, NDJSON API, 경영자문 직접 대화의 개념 질문(Tool 없음) / 실제 검토(Tool 사용), delegation 경로의 Tool 검사 |

---

## 10. 현재 구현과 목표 구조의 차이

| 영역 | Current implementation | Target architecture |
| --- | --- | --- |
| Capability 정의 | 각 system의 `registry.py`에서 Agent별 Skill / Tool subset 제공. frontend에 Workflow 이름 상수 없음 | 같음 |
| K Super 그래프 | `tools → model` 루프. 모델이 Tool 없이 응답하면 `END`, 그 응답이 종합 결과 | 같음 |
| Group Super | REAL (Group system + Group UI) | 같음 |
| A2A | REAL: Group Tool → K `/api/a2a/run` HTTP, 최종 응답만 Agent에 전달, lifecycle은 관찰용 relay. Group UI가 Group backend를 쓴다 | 같음 |
| Meeting 데이터 | Postgres 회의 저장소(system별 schema, fixture로 seed, 번호 migration). 회의록 · 결정 · Action Item · 진행 이력 · 일정 · 녹음 transcript(id로 가져와 1년 보관) · Compliance 검토 · 메일 발송함 · 다른 system의 업무 요청. 키워드 점수 조회 | + 의미 검색(임베딩), 메일 회신으로 진행 갱신 |
| 사용자 맥락 | gateway가 서명한 `principal_token`의 사용자를 run이 들고 Tool이 읽음 (A2A로 그대로 전달). 미팅 Tool만 사용 | 다른 Tool(뉴스 · 자문 자료 등급)도 같은 규칙 |
| News 데이터 | `data/news.json` 고정 fixture를 키워드로 조회 | 실제 뉴스 API / 검색 / RAG (`analyze_company_risk_news` 본문만 교체) |
| Advisory 데이터 | `data/companies.json` 고정 fixture + 결정적 규칙 evaluator | 실제 재무 모델 / 밸류에이션 / 실사 데이터 (evaluator만 교체) |
| Workflow 결과 렌더링 | `toolResultBlocks`가 Tool 이름별 `switch` (meeting, news, advisory 전용 렌더) | Tool이 늘어나도 frontend가 Tool별 지식을 과도하게 갖지 않는 방식 (미결정) |
| 상위 Agent로의 결과 전달 | 최종 응답 문자열만 `ToolMessage`. Advisory에 필요한 이전 결과는 K Super가 쓴 `task` 문장으로만 전달. 종합도 K Super가 history의 텍스트만 보고 쓴다 | 필요 시 범용 `artifacts` / `context` 구조 (미결정) |
| K Super 최종 응답의 frontend 표시 | `agent_end.response` 문자열을 그대로 메시지로 표시 (줄바꿈 유지) | 같음 또는 구조화 표시 (미결정, 12장 4·5번) |
| 오류 귀속 | `AgentError`가 없으면 run의 `root_agent`로 귀속 | 실행 중인 Agent에 정확히 귀속 |

---

## 11. 목표 구조와 전환 계획 (2026-10-12 결정)

Step 7·8을 real로 바꾸는 대신, 시스템을 **같은 플랫폼 기반 위의 독립된 Agent System 두 개**로 재편한다.
이 장은 결정된 목표와 단계다. 각 단계가 끝나면 그 시점의 사실을 8장·10장과 이 장의 상태 표에 반영한다.

### 목표 구조

| 실행 단위 | 성격 | 내용 | 이벤트 소스 |
| --- | --- | --- | --- |
| K Electronics backend (현 :8000) | 제품 | 공통 runtime + K system. 직접 채팅 4개(K Super 포함), A2A 수신 표면, 관찰 채널 | 자기 run |
| Group backend (:8001) | 제품 | 공통 runtime + Group system. 직접 채팅 4개, K전자에 대한 A2A client, 관찰 채널 | 자기 run |
| Group UI | 제품 | 탭 4개(Group Super · 경영자문 · 뉴스 · 미팅) + 협업 세션창. 그래프 없음 | Group backend의 요청자 NDJSON 스트림 |
| K UI | 제품 | 탭 4개(K Super · 경영자문 · 뉴스 · 미팅) + 협업 세션창. 그래프 없음. 외부 A2A 요청은 K Super 탭에 수신 카드 + Sidebar 배지 | K backend의 요청자 스트림 + 관찰 채널(`origin: a2a` run만) |
| Demo Monitor | 테스트·발표 전용 | 그래프 · Step 1~8 · 노드 상태 · A2A/위임 edge. 제품 navigation에 없는 별도 URL | Group backend의 관찰 채널 |

소유 경계:

| 영역 | 공통 runtime | 공용 Capability 라이브러리 | 각 system 소유 |
| --- | --- | --- | --- |
| Agent core | Specialist core · Super core · invocation 개념 | | `AGENT_ID` · `IDENTITY` · SPEC |
| Capability | `Skill` / `Capability` 타입, Registry 메커니즘 | 회의 · 외부 리스크 · M&A 검토 · 위임 Skill 본문, 데이터 경로를 받는 workflow 함수, Tool factory | Registry 내용(`CAPABILITIES`, `allowed_agents`), 라이브러리 Tool을 자기 데이터에 바인딩한 instance, `delegate_to_*`, A2A client Tool |
| Data | | | fixture 파일 (K와 Group이 같은 구조의 데이터를 따로 가진다. Group이 K 데이터를 읽지 않는다) |
| 이벤트 · API | emit / run context(run_id · system · origin · trace) / stream_run / 관찰 hub + SSE / NDJSON 래퍼 / A2A 요청·응답 형식 | | 엔드포인트 목록, 포트, 오류 귀속 기본 Agent |
| Frontend | NDJSON client · SSE client · mapper · reducer · Sidebar/ChatPanel/SessionWindows · 표시 이름 catalog | | 플랫폼 설정(탭, 헤더, backend 주소), Tool별 결과 렌더 |

두 system은 서로의 Agent · Tool · Workflow · Data · Registry를 import하거나 호출하지 않는다 (import 경계 테스트로 고정).

### 결정 사항

1. **A2A 프로토콜.** Group Super의 Capability Tool(K전자 검토 요청)이 K backend의 A2A 수신 엔드포인트를 HTTP로 호출한다. K는 `POST /api/a2a/run`에서 NDJSON lifecycle 스트림을 돌려주고 `agent_end(k_super).response`가 최종 응답이다.
   Group Super **Agent**는 최종 응답 텍스트만 `ToolMessage`로 받는다. K의 lifecycle 이벤트는 Group Tool이 **관찰 정보로** 자기 run에 다시 emit한다(`system` 태그 + relayed 표시). Group UI는 이것으로 A2A 세션 안에 K의 위임 세션을 중첩해 그린다.
2. **관찰 채널.** runtime의 프로세스 내 hub가 `stream_run`의 이벤트를 복사하고, 각 backend가 `GET /api/events`를 SSE로 연다. 요청자 스트림은 그대로 유지한다.
3. **Agent id.** `meeting` · `news` · `advisory` 같은 id는 system 내부 이름으로 유지하고, runtime이 모든 이벤트에 `system` 태그를 붙인다. id 개명은 하지 않는다.
4. **Super Agent invocation.** Specialist와 같은 규칙: `direct`(사용자 직접 대화)는 근거 검사 없이 Skill 지침에 따르고, `a2a`(A2A 수신)에서만 "위임 1회 이상 · 응답 비어 있지 않음" 검사.
5. **Group Super의 Capability.** A2A client Tool **과** 자기 Specialist 3개에 대한 `delegate_to_*` Tool을 모두 가진다. NeuroEdge 요청에서 K전자 A2A를 택하는 것은 Registry 제한이나 하드코딩이 아니라 요청 내용과 Skill/Tool 설명에 따른 모델 판단이다.
6. **오류 전파.** K 실패는 Group에서 `k_super`로 귀속한 `AgentError`로 중단한다. mock 대체 없음.
7. **Specialist 재사용.** Specialist core · Skill 본문 · workflow 함수 · Tool의 이름/설명/schema는 공용 라이브러리. system마다 새로 쓰는 것은 정체성 문장, Registry 등록, 데이터 파일, Tool 바인딩뿐이다.
8. **Step 1~8.** backend에는 없다. Demo Monitor가 이벤트를 해석한다: Group `agent_start` → 1, `a2a_start` → 2, relayed `delegation_start`(meeting/news/advisory) → 3/4/5, relayed `agent_end(k_super)` → 6, `a2a_end` → 7, Group `agent_end` → 8.
9. **Frontend.** Vite 앱 하나, URL 경로로 Group UI · K UI · Demo Monitor 선택, dev proxy 둘. 의존성 추가 없음.
10. **의존성.** backend runtime 의존성에 `httpx` 추가(A2A client · 프로세스 내 end-to-end 테스트).
11. **K UI의 외부 A2A 실행 표시 (결정 · 구현됨).** Sidebar의 K Super 항목에 "외부 요청 처리 중 · 그룹" 배지 + K Super 탭 전사에 "외부 A2A 요청 · 그룹" 세션 카드(요청문, 그 아래 열리는 위임 창, 응답 반환으로 접힘). 탭 전환이나 자동 창 띄우기는 없다.
12. **회의 저장소 (2026-10-27 결정 · 구현됨).** 미팅 에이전트의 회의 기록은 JSON fixture가 아니라 Postgres다: DB 하나에 system마다 schema와 DB role을 따로 둔다 (K `kelec`, Group `grp`).
    fixture는 seed로만 쓴다. backend 의존성에 `psycopg[binary]` 추가. Tool은 회의록 작성(초안 → 확정 → 정정), Action Item 진행 조회 · 갱신, 사내 일정(외부 캘린더 동기화 없음)까지 다섯 개.
    회의록 항목은 공공기록물법 시행령 제18조②(참석자 · 배석자, 안건, 발언 요지, 결정 사항과 표결 내용)와 상법 제391조의3(반대자와 반대 이유, 출석자 서명)을 기준으로 잡았다.
13. **요청자 전달 (2026-10-27 결정 · 구현됨, 2026-10-28 수정).** gateway가 run 요청에 `principal`을 담고, runtime은 그것을 `RunContext`에 두며, Tool이 읽는다 (모델 인자로 받지 않음).
    A2A는 principal을 넘기되 수신 측은 읽기만 한다 — 예외는 18번의 업무 요청 접수(`submit`)뿐이다.
14. **서명된 요청자 (2026-10-28 결정 · 구현됨, 12장 옛 13번).** gateway가 principal을 `PRINCIPAL_SIGNING_KEY`로 HS256 서명해 보내고 backend는 키가 있으면 서명된 값만 믿는다. A2A는 토큰을 그대로 전달한다.
    표준 라이브러리로 검증한다 (의존성 추가 없음). mTLS는 하지 않는다.
15. **확정 권한 (2026-10-28 결정 · 구현됨, 12장 옛 14번).** 회의록 확정 · 정정 · Compliance 검토 · 메일 재발송 · 취소 · 업무 요청 접수 = 그 회의의 의장 · 간사 + secretary · executive 그룹 (`MEETINGS_CONFIRM_GROUPS`).
    초안 저장은 지금처럼 미팅 에이전트를 쓸 수 있는 사용자 누구나. 의결기구(`formal`)의 표결 · 서명 강제는 아직 없다.
16. **녹음 transcript와 보존 기간 (2026-10-28 결정 · 구현됨, 12장 옛 15번).** speech-service가 transcript를 id로 30일 보관하고 그 id를 텍스트 첫 줄에 붙인다. 미팅 Tool은 id만 받아 내부망으로 가져와 복사한다.
    보존: transcript 1년(이후 결정 · Action Item의 근거 링크만 비워짐), Open WebUI의 원본 녹음 파일 30일(배포의 retention 작업). 회의록 · 결정 · Action Item은 계속 보관.
17. **사람에게 가는 채널 = 메일, Agent가 주소를 찾아 바로 발송 (2026-10-28 결정, 2026-10-30 변경 · 구현됨).** 처음에는 Agent가 만든 메일을 간사가 승인해야 나갔으나,
    2026-10-30 사용자 결정으로 승인 단계를 없앴다: Agent가 받는 사람의 주소를 스스로 찾고(로그인 등록 이메일 → 임직원 · 외부 연락처), 메일을 작성해 바로 보낸다 (기한 알림 포함).
    주소를 못 찾으면 추측하지 않고 매일 다시 찾는다. 사람은 실패 · 오류가 있을 때만 다시 보내기 · 취소 · 주소 정정을 한다. 메일 회신을 읽어 진행을 갱신하는 것은 아직 없다.
18. **A2A 업무 요청 (2026-10-28 결정 · 구현됨).** 다른 system의 A2A run은 업무 요청을 `pending`으로 접수만 할 수 있고, 수신 system의 확정 권한자가 확인해야 정식 Action Item이 된다.
    배포(Open WebUI)에는 K 쪽 확인 창구가 없다: 2026-10-28에 둔 "K전자 회의지원 Agent"는 2026-10-30에 메뉴를 회의지원 하나로 합치면서 뺐다 (사용자 결정).
    따라서 배포에서 보낸 업무 요청은 K 저장소에 `pending`으로 남는다. 확인은 K system의 미팅 에이전트를 직접 쓰는 화면(Vite K UI, 결과는 JSON — 12장 17번)에서만 된다.
19. **회의 저장소 migration (2026-10-28 결정 · 구현됨, 12장 옛 16번).** 번호 붙은 SQL 파일 + schema별 `schema_migrations` (새 도구 없음). `meeting_schema.sql`은 version 1로 고정한다.
20. **Super Agent의 권한 · 기록 작업 (2026-10-30 결정 · 구현됨).** gateway가 principal(서명 토큰 포함)에 사용자가 쓸 수 있는 전문 Agent(`agents`)를 담는다.
    위임 Tool(`library/tools/delegation`)은 그 밖의 Agent에는 위임하지 않고 `delegation_skipped`를 보낸 뒤 Super Agent에게 "권한이 없어 빠졌다고 밝히라"는 결과를 돌려준다 —
    전에는 gateway가 권한 없는 `delegation_start`에서 응답을 끊었다 (그 검사는 안전장치로 남김). A2A로 받은 K Super의 위임도 같은 토큰으로 거른다.
    Skill `specialist_delegation`: 조회 · 분석만 위임하고, 기록 · 발송(회의록 확정, 일정, 메일, 업무 전달)은 요청이 시켰을 때만 — 아니면 "다음에 할 수 있는 일"로 제안한다.

### 전환 단계와 상태

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0 | 결정 문서화 (이 장) | 완료 |
| 1 | 동작 변화 없는 runtime 정비: Super core 추출(invocation) · run context · Registry 메커니즘/내용 분리 · workflow 데이터 경로 매개변수 | 완료 |
| 2 | 디렉터리 경계 이동(runtime / 라이브러리 / K system) + import 경계 테스트 | 완료 |
| 3 | 관찰 hub + SSE (별도 run 시작 이벤트는 두지 않음: root Agent의 `agent_start`에 `origin` · `caller`가 찍힌다) | 완료 |
| 4 | K Super의 두 표면: A2A 수신(`a2a`) · 직접 채팅(`direct`) | 완료 |
| 5a | Group system: Group Super + A2A client Tool + Group app | 완료 |
| 5b | Group Specialist 3개 + Group 데이터 + Group Super의 `delegate_to_*` | 완료 |
| 6 | Demo Monitor | 완료 |
| 7 | 제품 UI(Group · K) | 완료 |
| 8 | 정리 · 문서 (옛 단일 앱 · scenario mock · `/api/k-super/run` 제거, reducer를 `state/runState.ts`로) | 완료 |
| 8+ | K UI의 외부 A2A 실행 표시 (11번 결정, 12장 11번) | 완료 |
| 9 | 회의 후속 기능: 서명된 요청자 · 확정 권한 · Compliance 검토 · transcript 연결과 보존 · 메일 발송함과 기한 알림 · A2A 업무 요청 · migration (14~19번 결정) | 완료 (2026-10-28) |

각 Phase가 끝날 때 현재 데모가 그대로 동작해야 한다는 규칙으로 진행했다 (Phase 7 전까지는 옛 단일 frontend의 mock Step 1·2·7·8을 유지).

---

## 12. Current limitations / Next architectural work

구현 전에 결정이 필요한 지점이다. 코드에서 조용히 확정하지 말고 명시적으로 결정한다.

1. **Step 경계 (결정됨).** Step은 **Demo Monitor 전용** 표시 개념이다 (`src/monitor/steps.ts`, 11장 8번 표). backend와 제품 UI에는 Step이 없다.
2. **K Super 위임 정책 (Step 4에서 내린 결정과 남은 부분).** `tools → model` 루프로 바꿨고, 위임은 **순차**다: Skill이 "한 번에 하나"를 지시하고 `tests/test_agents.py`가 각 위임이 별도 model 턴에서 일어남을 검증한다.
   모델이 한 턴에 여러 tool call을 보내면 `run_tools`는 그대로 순차 실행한다 (거부하지 않음). 이벤트 순서는 그래도 순차다.
   **종료 조건 (Step 6에서 내린 결정)**: "허용된 위임 Tool을 모두 호출"하는 임시 규칙(`_after_tools`, `pending_delegations`)을 제거했다.
   매 `model` 턴이 다음 행동을 정하고, Tool 호출 없는 응답이 곧 종료다. 어떤 전문 Agent를 쓸지는 Skill `specialist_delegation`의 지침(요청의 항목 ↔ 위임 Tool 설명)에 따른 모델 판단이며,
   `tests/test_agents.py`가 NeuroEdge 요청(세 Agent 모두)과 회의 결정사항만 묻는 요청(Meeting만)으로 확인한다. 모델이 필요한 Agent를 빠뜨리거나 불필요한 Agent를 부르는 것을 막는 코드는 없다 (의도적).
   병렬 위임과 frontend session stack(중첩 전제)의 형제 세션 처리는 여전히 열려 있다. Group Super도 같은 core · 같은 정책이며, 자기 Specialist 위임과 K전자 A2A 중 무엇을 쓸지는 Tool 설명에 따른 모델 판단이다 (NeuroEdge 요청에서 K A2A를 고르고, K 응답 뒤 그룹 미팅 기록을 추가로 참조한 실행도 있었다).
3. **위임 Tool ↔ 대상 Agent의 순환 의존 (Step 5에서 내린 결정과 남은 부분).** `delegate_to_*` Tool은 대상 Agent의 `run_*`를 호출 시점에 import해서 순환을 끊는다 ([4장](#4-capability-registry)).
   Step 5에서 **공통 specialist core**, Phase 1에서 **공통 super core**를 도입했다 (`agent_core.SuperAgent` / `build_super_graph` / `run_super`, [3.1](#31-super-agent)). Agent 파일은 정체성과 Registry 조회만 갖는다.
   Phase 5b에서 위임 Tool이 6개(K 3 + Group 3)가 되면서 `library/tools/delegation.make_delegate_tool(name, description, from_agent, to_agent, run)` factory를 두었다. 각 system의 `delegate_to_*.py`는 Agent-visible 설명(`DESCRIPTION`)과 `FROM_AGENT` / `TO_AGENT`, runner 조회만 갖는다.
   `tests/test_agents.py`의 `_NoToolGraph`처럼 `build_*_agent`를 monkeypatch하는 테스트가 있으므로 `run_*_agent`는 그래프를 모듈 전역 이름으로 조회한다.
4. **Workflow 결과의 frontend 렌더링.** Tool별 `switch`를 계속 늘릴지, backend가 표시 힌트를 함께 보낼지, 범용 렌더로 갈지.
5. **상위 Agent로 전달할 구조화 결과 (Step 5에서 내린 결정과 남은 부분).** Step 5에서는 공유 구조를 만들지 않았다. Advisory가 필요로 하는 Meeting / News 결과는 K Super가 자기 history를 보고 **자연어 위임 문장에 직접 담는다** (Skill `specialist_delegation`의 지침, 코드 template 없음).
   Advisory Agent는 그 문장을 `run_mna_review`의 `context`(자유 텍스트)로 넘기고 workflow가 신호를 추출한다
   (가격 상한은 금액 뒤의 "초과 · 이하 · 상한 …"으로 찾되, "2조 원 초과 건은 본회의 상정"처럼 심의 경로를 정하는 문장은 가격 단어가 없으면 상한으로 보지 않는다 — 2026-10-30 수정). 따라서 K Super가 무엇을 옮겨 적는지, Advisory가 무엇을 `context`에 넣는지는 모델 판단이며 실행마다 다를 수 있다 (`tests/test_agents.py`가 가격 기준 · 리스크 주제가 전달됐는지만 검증).
   Step 6 종합은 **텍스트만으로 구현했다**: K Super는 history의 `ToolMessage` 텍스트를 보고 최종 응답을 쓰고, frontend는 그 문자열을 그대로 표시한다.
   A2A도 텍스트만이다: Group Super는 K의 최종 응답 문자열을 `ToolMessage`로 받아 그룹 판단을 쓴다. 구조화된 결과(권고 코드, 조건 목록 등)가 필요해지면 `artifacts` / `context` 같은 공유 구조와 A2A payload 확장을 함께 본다.
6. **Group Super와 A2A (결정 · 구현됨).** 별도 프로세스(Group backend)의 Group Super가 Capability Tool로 K backend의 A2A 수신 표면을 HTTP 호출한다 ([3.4](#34-agent-delegation과-a2a의-차이), [11장](#11-목표-구조와-전환-계획-2026-10-12-결정)).
   남은 것: A2A 프로토콜 자체는 이 레포 안의 약속(NDJSON lifecycle + 최종 응답)이며 표준 A2A 프로토콜은 아니다. K의 주소는 환경변수(`K_ELECTRONICS_A2A_URL`) 하나이고 peer 목록 · 인증 · 재시도는 없다.
7. **오류 귀속.** `stream_run`이 `AgentError` 외 예외를 run의 `root_agent`로 귀속한다 (Phase 1). 실행 중인 하위 Agent까지 정확히 지목하려면 실행 중 Agent 추적이 필요하다 (미해결). A2A 경계를 넘는 실패는 Group 쪽에서 `k_super`로 귀속되고, K의 원래 오류는 relay된 이벤트로 함께 보인다.
8. **Agent 식별자 동기화.** backend `AGENT_ID` 문자열과 frontend `AgentId`가 수동으로 일치해야 한다. frontend는 모르는 id의 이벤트를 조용히 버린다. `monitor/steps.ts`와 `platform/config.ts`도 같은 id를 쓴다.
   backend 안에서도 `systems/k_electronics/registry.py`(`allowed_agents`)와 `systems/k_electronics/tools/delegate_to_*.py`(`from` / `to`)가 같은 문자열을 리터럴로 가진다. Registry가 Agent 모듈을 import할 수 없기 때문이며, `tests/test_capabilities.py`가 일치를 검증한다.
   **2026-10-12 결정 · 구현됨**: 두 system이 같은 Specialist id를 쓰므로 id는 system 내부 이름으로 두고 runtime이 이벤트에 `system` 태그를 붙인다. frontend는 `resolveAgent(system, agent)`로 화면용 id를 정한다 (Group의 Specialist는 `group_*`).
9. **Advisory persona 선택과 다중 관점.** persona는 `run_mna_review`의 인자이고 어떤 관점을 쓸지는 Advisory Agent의 모델 판단이다 (Skill: 한 번의 검토에 관점 하나, 지정 없으면 CEO).
   초기 실행에서 K Super가 "CEO/CFO/CTO 관점"을 모두 요구해 Advisory가 Tool을 세 번 호출한 사례가 있어 Skill과 위임 Tool 설명을 "관점 하나"로 조였다. 여러 관점의 비교 · 토론(CFO vs CTO)은 범위 밖이며, 필요해지면 Tool을 여러 번 호출할지, workflow가 다중 persona를 한 번에 돌려줄지 결정한다.
10. **Advisory 호출 맥락의 2중 전달.** K Super → Advisory는 `task` 문장, Advisory → Tool은 `context` 인자로 같은 맥락이 두 번 LLM을 거친다. Advisory가 `context`를 요약하거나 빠뜨리면 workflow의 신호 추출이 약해진다 (Skill이 "그대로 넣으라"고 지시). 공유 구조(5번)를 도입하면 함께 해소된다.
11. **K UI에서 외부 A2A 실행의 표시 (결정 · 구현됨).** `src/platform/useInboundRuns.ts`가 K의 `/api/events`를 구독해 `origin: a2a` run을 K Super 탭 상태에 같은 mapper · reducer로 흘려 넣는다(세션 카드 + 위임 창 + 응답 반환), Sidebar에는 배지. 배지는 전사 재생이 따라잡은 뒤 꺼진다. 남은 제약: 사용자가 K Super 탭에서 직접 대화하는 동시에 외부 요청이 오면 한 전사에 섞인다.
12. **Demo Monitor의 소스.** Monitor는 Group 관찰 채널 하나만 구독한다(relay 덕분에 K lifecycle 포함). K UI에서 직접 시작한 K 실행은 Monitor에 보이지 않는다. 필요해지면 K 채널도 구독하고 relay된 복사본을 걸러낸다.

---

13. ~~principal의 신뢰 경계~~ → 11장 14번으로 결정 · 구현 (서명된 `principal_token`). 남은 것: 키 회전 절차, 내부망 전송 자체의 암호화(mTLS)는 없다.
14. ~~회의 기록 쓰기 권한의 세분화~~ → 11장 15번. 남은 것: 의결기구(`meeting_series.formal`)의 표결 · 출석자 서명 강제는 schema에만 있다.
    참석자 role(의장 · 간사)은 초안을 쓰는 사람이 적을 수 있으므로, 한 호출 안에서 자기를 간사로 적고 확정하는 것만 막는다 (다음 호출에서는 인정된다). role 지정 자체를 확정 권한자로 제한할지는 미결정.
15. ~~녹음 transcript와 보존 기간~~ → 11장 16번.
16. ~~회의 저장소 schema 변경~~ → 11장 19번.
17. **새 미팅 Tool 결과의 frontend 렌더.** Vite 제품 UI의 `toolResultBlocks`는 `run_meeting_context_retrieval`만 전용 렌더가 있고 나머지 여섯 Tool(`manage_mail_outbox` · `run_task_requests` 포함) 결과는 JSON으로 보인다 (4번과 같은 문제).
    배포의 사용자 화면은 Open WebUI라 Agent의 텍스트 응답으로 보인다.
18. **메일 회신과 진행 갱신.** 슬라이드의 "이메일 회신 → 진행 상황 갱신"은 없다 (IMAP 수신 · 회신과 Action Item 매칭 · 회신자 확인이 필요). 지금은 담당자가 Agent에게 직접 알린다.
19. **Compliance 규칙의 한계.** 키워드 · 정규식 규칙이라 돌려 말한 발언은 놓치고 일상 표현에도 걸릴 수 있다 (검토자가 cleared로 닫는다). LLM 판정을 더할지, 규칙을 법무팀이 관리하게 할지는 미결정.
20. **조직 이름 담당.** `resolve_owner`는 "팀 · 본부 · 실 …"로 끝나는 이름만 조직으로 보므로 "K전자" 같은 회사명 담당은 사람으로 등록되어 배정 메일이 주소 필요 상태가 된다.
    계열사에 배정된 업무는 A2A 업무 요청으로 전달하는 것이 맞는 경로다 (11장 18번).
21. **주제 추론의 한계.** 주제는 회의명 단어로 정하므로 회의명이 매번 다른 정기 회의("10/13 회의")는 회의마다 다른 주제가 되고, 회의명이 겹치면 다른 사업이 한 주제로 묶일 수 있다.
    Agent가 `save_meeting_minutes(subject=)`로 이름을 주면 그것을 쓴다. 주제를 사람이 고치는 화면은 아직 없다.

---

## 13. 이 문서의 유지

- Step이 real로 바뀌거나 architecture 결정이 내려지면 이 문서의 해당 표를 같은 커밋에서 갱신한다.
- "Current implementation"에는 코드에 있는 것만 적는다. 계획은 "Target architecture" 또는 12장에 적는다.
- 실행 방법과 파일별 상세는 `README.md`에 있다. 개발 규칙은 `AGENTS.md`, Claude 전용 지침은 `CLAUDE.md`다.
