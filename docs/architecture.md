# Architecture

이 문서는 이 repository의 architecture **Single Source of Truth**다.
"우리가 무엇을 만들고 있는지, 현재 어디까지 구현되어 있는지, 앞으로 어떤 구조를 지향하는지"를 설명한다.

문서 전체에서 **Current implementation**(지금 코드에 있는 것)과 **Target architecture**(지향하는 구조)를 구분해서 적는다.
둘이 다른 곳은 [10. 현재 구현과 목표 구조의 차이](#10-현재-구현과-목표-구조의-차이)에 모아 두었다.

기준 시점: 2026-10-11 (Step 3 real + Capability Registry, 나머지 mock).

---

## 1. 프로젝트 목적

대기업 C-level 경영진을 위한 **Multi-Agent Decision Support System / Agent Platform의 prototype**이다.
단순한 M&A 챗봇이나 고정 workflow demo가 아니다.

- 경영진은 자연어로 요청한다.
- 그룹 Super Agent가 요청을 이해하고 계열사 Super Agent에게 A2A로 검토를 요청한다.
- 계열사 Super Agent는 자기 전문 Agent들에게 업무를 위임하고 결과를 종합한다.
- 전문 Agent는 자신에게 허용된 Workflow Tool로 실제 데이터를 조회·분석한다.

현재는 이 구조를 **NeuroEdge 인수 검토**라는 고정 scenario 하나로 시연하면서,
mock으로 재생되던 단계를 한 Step씩 실제 Agent 실행으로 교체하는 중이다 ([7장](#7-개발-전략-mock--real-vertical-slice)).

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
그룹 슈퍼에이전트 (Group Super)          ← frontend mock
       ⇅ A2A                             ← frontend mock
K전자 슈퍼에이전트 (K Electronics Super)  ← backend REAL (LangGraph)
       ↓ delegation
       ├── 미팅 에이전트 (Meeting)         ← backend REAL (LangGraph)
       ├── 뉴스 에이전트 (News)            ← frontend mock
       └── 경영자문 에이전트 (Advisory)    ← frontend mock
```

Agent 식별자는 backend와 frontend가 같은 문자열을 쓴다:
`chairman`, `group_super`, `k_super`, `meeting`, `news`, `advisory`
(frontend: `src/types/agent.ts`의 `AgentId`, backend: 각 agent 모듈의 `AGENT_ID`와 `capabilities/registry.py`의 `allowed_agents`).

---

## 3. 핵심 개념

### 3.1 Super Agent

Super Agent는 단순 workflow router가 아니다.
자연어 요청을 이해하고, **어떤 전문 Agent에게 무엇을 맡길지 스스로 판단**하며, 전문 Agent에게 **자연어로** 업무를 위임한다.

```text
K Electronics Super
    ├─ delegate_to_meeting_agent(task: str)
    ├─ delegate_to_news_agent(task: str)       ← Target
    └─ delegate_to_advisory_agent(task: str)   ← Target
```

- 별도의 중앙 Planner / Router 노드가 판단을 대신하는 구조는 기본 architecture로 두지 않는다. **Agent 자체가 판단 주체**다.
- 위임은 LLM의 실제 tool calling으로 일어난다. 위임 문장(`task`)은 LLM이 작성하므로 실행마다 달라질 수 있다.
- Super Agent는 직접 분석하거나 답을 지어내지 않는다 (K Super system prompt 규칙).

**Current implementation** — `backend/app/agents/k_super.py`
- LangGraph `StateGraph(MessagesState)`, 노드 `model` / `tools`.
- Registry가 허용한 Capability: Skill `specialist_delegation`(위임 지침), Tool `delegate_to_meeting_agent`. 둘 다 `backend/app/capabilities/`에 있다 ([4장](#4-capability-registry)).
- 그래프가 `tools → END`로 끝난다. 즉 Step 3 범위인 **위임 1회**만 수행하고, 종합(Step 6)은 아직 없다.
  `run_k_super()`의 반환값(K Super 자신의 마무리 응답)은 현재 빈 문자열이다.
- 위임 Tool을 하나도 호출하지 않으면 `AgentError("k_super", ...)`로 실패 처리한다.

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

**Current implementation** — `backend/app/agents/meeting.py`
- LangGraph `StateGraph(MessagesState)`, `model ⇄ tools` 루프 (`tools → model`).
- Registry가 허용한 Capability: Skill `meeting_context_analysis`(조회·정리 지침, 응답 형식), Tool `run_meeting_context_retrieval`.
- Workflow Tool을 호출하지 않고 답하면 `AgentError("meeting", ...)`. 데이터에 근거하지 않은 답은 실패로 본다.
- 두 경로 모두 같은 `run_meeting_agent(task)`를 호출한다.
  delegation 경로는 K Super의 tool 안에서, 직접 대화 경로는 `POST /api/agents/meeting/run`에서.
- News / Advisory Agent는 backend에 없다. frontend에서 mock으로만 재생된다.

### 3.3 Workflow Tool

Workflow Tool은 **Agent가 아니다**.
전문 Agent가 실제 업무를 수행하기 위해 사용하는 **고정적이거나 반고정적인 업무 절차**이며, Registry 관점에서는 **Tool Capability**다 ([4장](#4-capability-registry)).

```text
Meeting Agent   → run_meeting_context_retrieval
News Agent      → run_company_risk_news_analysis   (Target)
Advisory Agent  → run_mna_review                   (Target)
```

- Agent는 **어떤** capability를 쓸지 판단한다. 선택된 Tool **내부**의 반복적·결정적 절차는 LLM 자유 추론에 맡기지 않는다.
- `run_mna_review` 내부에는 향후 `재무 분석 → 기업가치 산정 → 전략적 적합성 → 리스크 분석 → 권고안` 같은 workflow가 캡슐화될 수 있다.
  이 내부 단계를 Agent-visible Tool 여러 개로 쪼갤 필요는 없다.
- Workflow Tool은 데이터 경계이기도 하다. Agent는 fixture / DB를 직접 만지지 않는다.

**Current implementation** — Agent-visible Tool과 내부 workflow를 분리한다.
- `backend/app/capabilities/tools/run_meeting_context_retrieval.py`: `@tool("run_meeting_context_retrieval")`, 인자 `query: str, topics: list[str] | None`. Agent에게 보이는 표면(이름·설명·schema)만 가진다.
- `backend/app/workflows/meeting_context_retrieval.py`: 내부 구현 workflow. 순수 함수 `retrieve_meeting_context()`가 `backend/app/data/meetings.json`을 키워드 점수(topic 3점, title 2점, decision 1점, 절대/상대 threshold)로 조회한다. LLM 없이 테스트한다.
- 반환은 dict (`matched`, `meetings[]`에 `decisions`, `open_action_items`).
- 향후 `run_mna_review`처럼 여러 단계가 있는 Tool도 같은 형태다: `capabilities/tools/`에 Tool 하나, `workflows/`에 단계들.

### 3.4 Agent Delegation과 A2A의 차이

이 프로젝트에서 둘은 **같은 의미가 아니다**.

| 구분 | 의미 | 예 | frontend `CommunicationType` |
| --- | --- | --- | --- |
| **Internal Agent Delegation** | 같은 조직/시스템 내부에서 Super Agent가 자기 전문 Agent에게 업무를 맡기는 것 | K Super → Meeting / News / Advisory | `delegation` |
| **A2A** | 독립된 Agent System 또는 조직 경계를 넘는 통신 | Group Super ⇅ K Super | `a2a` |

- Specialist Agent 호출을 A2A라고 부르지 않는다.
- Delegation은 backend에서 `delegation_start` / `delegation_end` 이벤트로 드러난다. 일반 `tool_start`로 보고하지 않는다. UI 입장에서 위임은 Agent 간 handoff이기 때문이다.

**Current implementation**
- Delegation: K Super에 허용된 Tool `delegate_to_meeting_agent`(`capabilities/tools/`)가 같은 프로세스 안에서 `run_meeting_agent(task)`를 직접 호출한다.
- A2A: backend에 **없다**. Group Super는 backend에 구현되어 있지 않고, Step 2의 A2A 요청은 frontend mock 세션이다.
  Step 3에서 frontend가 그 A2A 요청 문장(`A2A_REQUEST_TEXT`)을 `POST /api/k-super/run`에 그대로 보내는 것이 현재의 "A2A"다.
  A2A 프로토콜은 범위 밖이다.

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

### Current implementation — `backend/app/capabilities/`

```text
capabilities/
  base.py        Skill(name, description, instructions) · Capability(impl, allowed_agents) — kind는 impl 타입으로 결정
  registry.py    CAPABILITIES 튜플 (권한의 source of truth) · get_capability / capabilities_for / skills_for / tools_for
  skills/        Skill 정의 (specialist_delegation.py, meeting_context_analysis.py)
  tools/         @tool 정의 (delegate_to_meeting_agent.py, run_meeting_context_retrieval.py)
workflows/
  meeting_context_retrieval.py   Tool 내부 구현 workflow (순수 함수, LangChain 의존 없음). Agent-visible 아님
```

| Capability | kind | allowed_agents | 구현 |
| --- | --- | --- | --- |
| `specialist_delegation` | skill | `k_super` | `capabilities/skills/specialist_delegation.py` |
| `delegate_to_meeting_agent` | tool | `k_super` | `capabilities/tools/delegate_to_meeting_agent.py` → `agents/meeting.run_meeting_agent` |
| `meeting_context_analysis` | skill | `meeting` | `capabilities/skills/meeting_context_analysis.py` |
| `run_meeting_context_retrieval` | tool | `meeting` | `capabilities/tools/run_meeting_context_retrieval.py` → `workflows/meeting_context_retrieval.retrieve_meeting_context` |

설계 규칙:

- **Tool은 LangChain `BaseTool` 그 자체**다. 이름 · description · argument schema는 `@tool` 정의가 source of truth이고 Registry는 이를 중복 정의하지 않는다.
- **Skill은 `Skill` dataclass**(이름, 설명, 지침 텍스트)다. Agent 고유의 정체성 / 출력 형식 규칙은 Agent 파일의 `IDENTITY`에 남고, "이 업무를 어떻게 수행하는가"만 Skill이다.
- **권한(`allowed_agents`)은 `registry.py`에만** 있다. Skill / Tool 모듈은 자기가 누구에게 허용되는지 모른다.
- Agent 파일은 `skills_for(AGENT_ID)` / `tools_for(AGENT_ID)`만 호출한다 (`k_super.py`, `meeting.py`의 `SYSTEM_PROMPT`, `TOOLS`).
  Skill / Tool 모듈을 Agent 파일에서 직접 import하지 않는다.
- 위임 Tool은 대상 Agent의 `run_*` 함수를 **호출 시점에 import**한다. 대상 Agent도 Registry를 쓰므로 module-level import는 순환이 된다.
  이 덕분에 `k_super.py`는 `meeting.py`를 import하지 않는다.
- Registry는 import 시 이름 중복과 빈 `allowed_agents`를 거부한다. `tests/test_capabilities.py`가 Registry 내용과 Agent ↔ Registry 일치를 검증한다.

frontend에는 mock Step 4·5 표시용 Workflow 이름 상수(`src/scenario/mockData.ts`의 `WORKFLOWS`, `src/types/agent.ts`의 `AGENTS[id].workflow`)가 남아 있다.
real Step(3)에서는 backend `tool_start` 이벤트의 실제 이름을 쓴다. 이 상수는 해당 Step이 real이 되면 없어진다.

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

Meeting Agent MessagesState   ← 별도 그래프, 별도 history
  Human(task)
  AI(tool_call: run_meeting_context_retrieval)
  Tool(조회 결과 JSON)
  AI(최종 응답)
```

- Meeting Agent 내부의 LLM 메시지 / Tool Call / Tool Result는 K Super history에 **병합하지 않는다**.
- 상위 Agent에는 필요한 결과(최종 응답)만 `ToolMessage`로 전달한다.

**Current implementation**: 위 그대로다. `delegate_to_meeting_agent`는 `run_meeting_agent()`의 반환 문자열만 돌려주고, K Super는 그것을 `ToolMessage`로 받는다.
Tool 결과의 구조화 데이터(dict)는 이벤트(`tool_end.result`)로 UI에 전달될 뿐, 상위 Agent state에는 들어가지 않는다.

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

`backend/app/events.py`

- Agent 코드는 실제 lifecycle 시점에 `emit(event)`를 호출한다. emitter는 `contextvars`로 주입되어 실행 밖에서는 no-op이다.
- `stream_run(fn)`은 Agent를 worker thread에서 돌리고 이벤트를 발생 즉시 yield한다. 마지막은 `done` 또는 `error`.
- `collect_events(fn)`은 테스트용 동기 수집기다.
- `AgentError(agent, message)`로 실패한 Agent를 지목한다. 지목되지 않은 예외는 `k_super`로 귀속된다.

| 이벤트 | payload | 발생 위치 |
| --- | --- | --- |
| `agent_start` | `agent, request` | `run_k_super`, `run_meeting_agent` 시작 |
| `delegation_start` | `from, to, task` | `delegate_to_meeting_agent` 진입 |
| `tool_start` | `agent, tool, args` | `common.execute_tool_calls` (Specialist의 Workflow Tool) |
| `tool_end` | `agent, tool, result` | 위 Tool 실행 직후 |
| `agent_end` | `agent, response` | 각 Agent 정상 종료 |
| `delegation_end` | `from, to, response` | 위임 Tool 반환 직전 |
| `error` | `agent, message` | `stream_run`이 예외를 변환 |
| `done` | – | 정상 종료 |

Step 3 한 번의 실제 이벤트 순서 (`tests/test_agents.py`가 검증):

```text
agent_start(k_super) → delegation_start(k_super→meeting) → agent_start(meeting)
→ tool_start → tool_end → agent_end(meeting) → delegation_end → agent_end(k_super) → done
```

### Current implementation — API

`backend/app/main.py` (FastAPI)

| Endpoint | 역할 |
| --- | --- |
| `GET /api/health` | 상태 확인 |
| `POST /api/k-super/run` `{request}` | Step 3. K Super 실행, lifecycle을 NDJSON으로 스트리밍 |
| `POST /api/agents/meeting/run` `{request}` | 사용자 → Meeting Agent 직접 대화. 같은 Agent core |

### Current implementation — frontend 반영

- `src/live/api.ts` — NDJSON 스트림 클라이언트. `error` 이벤트는 `LiveRunError(agent, message)`로 던진다.
- `src/live/agentEvents.ts` — backend 이벤트 → 기존 `ScenarioEvent`로 변환. 예약·추측 없이 도착한 이벤트만 상태로 바꾼다.
  Workflow 결과 렌더링(`toolResultBlocks`)은 Tool 이름별 `switch`이고, 모르는 Tool은 JSON으로 표시한다.
- `src/hooks/useScenarioRunner.ts` — reducer가 `nodes / edges / messages / sessions` 상태를 만든다.
  `live` 이벤트는 실행 중 도착하는 이벤트를 **도착 순서대로 즉시 적용**한다. 이벤트의 `delay`는 상태 변경을 늦추지 않고 다음 이벤트만 보류한다.
- 실패 시 backend가 지목한 Agent 노드를 error로 표시하고 scenario를 **중단**한다. mock으로 대체하지 않는다.

| backend 이벤트 | UI |
| --- | --- |
| `agent_start`(super) | 노드 status "요청 분석 중..." |
| `delegation_start` | delegation 세션 열림(실제 위임 문장) · 대상 노드 active · edge active |
| `tool_start` | 노드/세션에 실제 Workflow 이름, status "실행 중" |
| `tool_end` | 세션에 Workflow 인자·결과 메시지 |
| `agent_end`(specialist) | 세션에 최종 응답, 노드 completed, edge completed |
| `delegation_end` | 세션 닫힘, 위임자 status "결과 수신" |
| `error` | 해당 노드 error, 오류 메시지, scenario `error` 상태 |

직접 대화(Sidebar "미팅" 탭)는 `App.tsx`가 `runDirectLive` + 같은 mapper로 처리한다. News / Advisory 탭은 아직 mock(`getMockResponse`, `runDirect`)이다.

---

## 7. 개발 전략: mock → real vertical slice

전체 시스템을 한 번에 구현하지 않는다.
이미 만들어진 deterministic 8-step demo를 **한 Step씩 실제 실행으로 교체**한다.

```text
지금            다음              그 다음
Step 1 MOCK     Step 1 MOCK       ...
Step 2 MOCK     Step 2 MOCK
Step 3 REAL     Step 3 REAL
Step 4 MOCK  →  Step 4 REAL
Step 5 MOCK     Step 5 MOCK
...
```

- 기존 UI와 scenario shell(`scenarioEvents.ts`, AgentGraph, Session UI)은 최대한 유지하고 내부 실행부만 실제 Agent로 바꾼다.
- 한 Step을 real로 바꿀 때 그 Step의 mock 이벤트를 `live` 이벤트로 치환하고, 나머지 Step은 그대로 둔다.
- mock Step은 **의도된 임시 상태**다. 교체 전까지는 frontend 상수(`WORKFLOWS`, `MNA_REVIEW_STAGES` 등)로 재생된다. 이것이 6장 원칙에 아직 미치지 못하는 부분이며, Step이 real이 될수록 해소된다.
- 실패 시 mock fallback은 하지 않는다. backend가 꺼져 있거나 Agent가 실패하면 scenario는 오류 상태로 멈춘다.

---

## 8. 현재 8-step scenario와 구현 상태

고정 scenario: **NeuroEdge(뉴로엣지) 인수 검토**. `src/scenario/scenarioEvents.ts`.
Group Super 탭에서 `NeuroEdge` / `뉴로엣지` / `인수` 키워드가 들어간 요청을 보내면 시작한다.

| Step | 내용 | 상태 | 실행 주체 |
| --- | --- | --- | --- |
| 1 | 그룹 회장 → Group Super. 인수 검토 요청 | **MOCK** | frontend 이벤트 |
| 2 | Group Super → K Super. A2A 인수안 검토 요청 | **MOCK** | frontend 이벤트 (A2A 세션 열림) |
| 3 | K Super → Meeting Agent → `run_meeting_context_retrieval` | **REAL** | backend `POST /api/k-super/run`, LangGraph, Ollama Cloud |
| 4 | K Super → News Agent → `run_company_risk_news_analysis` | **MOCK** · 다음 구현 대상 | frontend 이벤트 |
| 5 | K Super → Advisory Agent → `run_mna_review` | **MOCK** | frontend 이벤트 (내부 단계는 status 텍스트로만) |
| 6 | K Super가 전문 Agent 결과 종합 | **MOCK** | frontend 이벤트 |
| 7 | K Super → Group Super. A2A 응답 | **MOCK** | frontend 이벤트 (A2A 세션 닫힘) |
| 8 | Group Super → 회장. 그룹 투자심의 최종 판단 | **MOCK** | frontend 이벤트 |

직접 대화 탭:

| Sidebar 탭 | 상태 |
| --- | --- |
| 그룹 슈퍼에이전트 | scenario 트리거. 비매칭 입력은 고정 안내문 |
| 미팅 | **REAL** (`POST /api/agents/meeting/run`) |
| 뉴스 | MOCK |
| 경영자문 | MOCK |

K Electronics Super는 Sidebar에 없고 그래프에서만 보인다.

---

## 9. Step 3의 현재 architecture

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
```

코드 대응:

| 단계 | 코드 |
| --- | --- |
| frontend가 Step 3 진입 | `scenarioEvents.ts`의 `type: "live"` 이벤트가 `streamAgentRun(K_SUPER_RUN_PATH, A2A_REQUEST_TEXT)` 호출 |
| API | `main.py` `k_super_run` → `stream_run(lambda: run_k_super(request))` |
| K Super LLM 판단 | `k_super.py` `_call_model`: `SystemMessage(SYSTEM_PROMPT)` + `bind_tools(TOOLS)`. 둘 다 Registry에서 `skills_for` / `tools_for("k_super")`로 받은 것 |
| 위임 | `k_super.py` `_run_tools` → `capabilities/tools/delegate_to_meeting_agent.invoke` → `delegation_start` emit → `run_meeting_agent(task)` |
| Meeting Agent LLM 판단 | `meeting.py` `_call_model` (Registry에서 받은 `SYSTEM_PROMPT`, `TOOLS`) |
| Workflow Tool 실행 | `common.execute_tool_calls` → `tool_start` emit → `capabilities/tools/run_meeting_context_retrieval.invoke` → `workflows/meeting_context_retrieval.retrieve_meeting_context` → `tool_end` emit |
| 응답 | `meeting.py` `final_response` → `agent_end` emit → 반환 → `delegation_end` emit → `ToolMessage` |
| UI 반영 | `agentEvents.ts` mapper → `useScenarioRunner` reducer |

LLM: `backend/app/llm.py` — `ChatOllama`, Ollama Cloud(`https://ollama.com`), 기본 모델 `deepseek-v4.1-flash`, `OLLAMA_API_KEY` 필요. 키는 `backend/.env`에 두고 Git에 넣지 않는다.

테스트:

| 파일 | LLM 필요 | 검증 내용 |
| --- | --- | --- |
| `backend/tests/test_workflow.py` | 아니오 | 내부 workflow 조회 로직 |
| `backend/tests/test_capabilities.py` | 아니오 | Registry 내용(kind, allowed_agents), Tool 정의가 source of truth, Agent가 Registry subset을 쓰는지, Skill이 system prompt에 들어가는지 |
| `backend/tests/test_events.py` | 아니오 | emit / collect / stream / error 귀속 |
| `backend/tests/test_agents.py` | 예 (`OLLAMA_API_KEY` 없으면 skip) | 이벤트 순서, 위임, NDJSON API |

---

## 10. 현재 구현과 목표 구조의 차이

| 영역 | Current implementation | Target architecture |
| --- | --- | --- |
| Capability 정의 | `capabilities/registry.py`에서 Agent별 Skill / Tool subset 제공. mock Step용 Workflow 이름이 frontend 상수에 남아 있음 | 모든 Step이 real이 되면 frontend 상수 제거, Registry만 남음 |
| K Super 그래프 | `tools → END`. 위임 1회 후 종료, 종합 없음 | `tools → model` 루프. 여러 Agent에 위임하고 결과 종합 (Step 6) |
| Group Super | backend 없음. frontend mock | 독립 Super Agent. K Super와 A2A |
| A2A | frontend가 A2A 요청 문장을 K Super API에 전달 | 독립 Agent System 간 통신 경계 |
| News / Advisory Agent | backend 없음. frontend mock + mock 결과 상수 | Meeting Agent와 같은 패턴의 LangGraph Agent + Workflow Tool |
| Step 4·5 UI | mock 이벤트가 `workflow` 이름과 내부 단계를 frontend 상수로 표시 | backend `tool_start` 등 실제 이벤트만 표시 |
| Workflow 결과 렌더링 | `toolResultBlocks`가 Tool 이름별 `switch` (meeting만 전용 렌더) | Tool이 늘어나도 frontend가 Tool별 지식을 과도하게 갖지 않는 방식 (미결정) |
| 상위 Agent로의 결과 전달 | 최종 응답 문자열만 `ToolMessage` | 필요 시 범용 `artifacts` / `context` 구조 (미결정) |
| 오류 귀속 | `AgentError`가 없으면 `k_super`로 귀속 | 실행 중인 Agent에 정확히 귀속 |

---

## 11. 다음 구현 대상: Step 4 (News Agent)

이번 문서화 시점에서 **아직 구현하지 않았다.** 패턴만 적는다.

```text
K Electronics Super
    ↓ delegate_to_news_agent(task)
News Agent
    ↓ run_company_risk_news_analysis(...)
News Data (fixture)
```

Step 3과 같은 패턴으로 확장한다. Advisory(Step 5)도 동일하다.

- backend:
  - `capabilities/tools/run_company_risk_news_analysis.py`(`@tool`) + `workflows/company_risk_news_analysis.py`(내부 구현) + 데이터 fixture
  - `capabilities/tools/delegate_to_news_agent.py` (`delegate_to_meeting_agent`와 같은 형태, `agents/news.run_news_agent`를 호출 시점에 import)
  - 필요하면 `capabilities/skills/`에 News 업무 지침 Skill. 의미 있는 지침이 있을 때만 만든다.
  - `capabilities/registry.py`의 `CAPABILITIES`에 위 항목을 `allowed_agents`(`news` / `k_super`)와 함께 추가. **이것이 유일한 등록 지점**이다.
  - `agents/news.py`: `AGENT_ID = "news"`, Meeting Agent와 같은 구조, `skills_for` / `tools_for`로 subset을 받음. `main.py`에 `POST /api/agents/news/run`.
  - K Super 코드는 바뀌지 않는다 (Registry에서 `delegate_to_news_agent`를 받음). 이벤트 종류는 새로 만들지 않는다.
- frontend: `scenarioEvents.ts`의 Step 4 mock 이벤트를 `live`로 교체, `agentEvents.ts`에 결과 렌더링 추가.
- Step 4를 real로 만들려면 K Super가 한 실행에서 두 Agent에 위임해야 한다. 이는 `tools → END`를 바꾸는 결정과 맞물린다 ([12장](#12-current-limitations--next-architectural-work)).

---

## 12. Current limitations / Next architectural work

구현 전에 결정이 필요한 지점이다. 코드에서 조용히 확정하지 말고 명시적으로 결정한다.

1. **K Super의 다중 위임과 scenario Step 경계.**
   현재 frontend의 "Step" 개념은 scenario 이벤트에만 있고 backend에는 없다. Step 3의 `live` 이벤트는 K Super 실행 한 번 전체를 감싼다.
   Step 4가 real이 되면 K Super 한 번의 실행이 Step 3·4(·5·6)를 모두 포함한다.
   Step 번호를 backend 이벤트(예: `delegation_start`의 대상 Agent)에서 유도할지, Step 개념을 UI 전용으로 유지할지 결정이 필요하다.
2. **K Super 그래프 루프.** `tools → model`로 바꾸면 LLM이 위임 순서와 횟수를 결정한다. 한 턴에 여러 tool call이 오면 현재 `_run_tools`는 순차 실행한다.
   순차/병렬 위임 정책과, 병렬일 경우 frontend session stack(중첩 전제)이 형제 세션을 어떻게 다룰지가 열려 있다.
3. **위임 Tool ↔ 대상 Agent의 순환 의존.** `delegate_to_*` Tool은 대상 Agent의 `run_*`를 호출 시점에 import해서 순환을 끊는다 ([4장](#4-capability-registry)).
   Agent 수가 늘어 이 패턴이 반복되면, 위임 Tool이 Agent 모듈을 직접 알지 않고 "agent id → runner" 조회를 거치는 형태(예: `agents/` 쪽의 작은 runner 테이블)로 바꿀지 결정한다. 지금은 Tool 2개 규모라 도입하지 않았다.
4. **Workflow 결과의 frontend 렌더링.** Tool별 `switch`를 계속 늘릴지, backend가 표시 힌트를 함께 보낼지, 범용 렌더로 갈지.
5. **상위 Agent로 전달할 구조화 결과.** Step 6 종합에 텍스트 응답만으로 충분한지, `artifacts` / `context` 같은 공유 구조가 필요한지.
6. **Group Super와 A2A.** 현재 범위 밖이지만, Step 7·8을 real로 바꾸려면 A2A 경계를 어디에 둘지(별도 프로세스/서비스인지, 같은 backend 안의 별도 Agent인지) 결정해야 한다.
7. **오류 귀속.** `stream_run`이 `AgentError` 외 예외를 `k_super`로 귀속한다. Agent가 늘면 실행 중인 Agent를 추적하는 방식이 필요하다.
8. **Agent 식별자 동기화.** backend `AGENT_ID` 문자열과 frontend `AgentId`가 수동으로 일치해야 한다. frontend는 모르는 id의 이벤트를 조용히 버린다.
   backend 안에서도 `capabilities/registry.py`(`allowed_agents`)와 `capabilities/tools/delegate_to_*.py`(`from` / `to`)가 같은 문자열을 리터럴로 가진다. Registry가 Agent 모듈을 import할 수 없기 때문이며, `tests/test_capabilities.py`가 일치를 검증한다.

---

## 13. 이 문서의 유지

- Step이 real로 바뀌거나 architecture 결정이 내려지면 이 문서의 해당 표를 같은 커밋에서 갱신한다.
- "Current implementation"에는 코드에 있는 것만 적는다. 계획은 "Target architecture" 또는 12장에 적는다.
- 실행 방법과 파일별 상세는 `README.md`에 있다. 개발 규칙은 `AGENTS.md`, Claude 전용 지침은 `CLAUDE.md`다.
