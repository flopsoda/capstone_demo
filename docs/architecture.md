# Architecture

이 문서는 이 repository의 architecture **Single Source of Truth**다.
"우리가 무엇을 만들고 있는지, 현재 어디까지 구현되어 있는지, 앞으로 어떤 구조를 지향하는지"를 설명한다.

문서 전체에서 **Current implementation**(지금 코드에 있는 것)과 **Target architecture**(지향하는 구조)를 구분해서 적는다.
둘이 다른 곳은 [10. 현재 구현과 목표 구조의 차이](#10-현재-구현과-목표-구조의-차이)에 모아 두었다.

기준 시점: 2026-10-11 (Step 3·4·5·6 real + Capability Registry, 나머지 mock).

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
그룹 슈퍼에이전트 (Group Super)          ← backend REAL (Group system, :8001) · frontend는 아직 mock (Phase 7)
       ⇅ A2A (HTTP)                      ← backend REAL (Group Tool → K `/api/a2a/run`) · frontend는 아직 mock
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
  근거 검사(위임 1회 이상)는 `a2a`에서만 한다. API: `POST /api/a2a/run`(= `/api/k-super/run`)이 `a2a`, `POST /api/agents/k-super/run`이 `direct` (Phase 4).
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

**Current implementation** — `backend/app/systems/k_electronics/agents/meeting.py`, `news.py`
**Current implementation** — `backend/app/runtime/agent_core.py` + `backend/app/systems/k_electronics/agents/{meeting,news,advisory}.py`
- 세 Agent는 `runtime/agent_core.py`의 **공통 specialist core** 하나를 쓴다: `Specialist(agent_id, display_name, system_prompt, tools)` 값 객체,
  `build_specialist_graph(spec)`(LangGraph `StateGraph(MessagesState)`, `model ⇄ tools` 루프), `run_specialist(spec, graph, task, invocation=...)`.
  Agent 파일에는 `AGENT_ID` · `IDENTITY` · Registry 조회(`SYSTEM_PROMPT`, `TOOLS`) · `build_*_agent()` · `run_*_agent()`만 있다.
- Registry가 허용한 Capability:
  - Meeting: Skill `meeting_context_analysis`(조회·정리 지침, 응답 형식), Tool `run_meeting_context_retrieval`.
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
Meeting Agent   → run_meeting_context_retrieval
News Agent      → run_company_risk_news_analysis
Advisory Agent  → run_mna_review
```

- Agent는 **어떤** capability를 쓸지 판단한다. 선택된 Tool **내부**의 반복적·결정적 절차는 LLM 자유 추론에 맡기지 않는다.
- `run_mna_review` 내부에는 `입력 정보 정리 → persona별 평가 기준 적용 → 항목별 평가 → 긍정/우려 요인 → 권고 → 조건` workflow가 캡슐화되어 있다.
  이 내부 단계를 Agent-visible Tool 여러 개로 쪼개지 않는다.
- Workflow Tool은 데이터 경계이기도 하다. Agent는 fixture / DB를 직접 만지지 않는다.

**Current implementation** — Agent-visible Tool과 내부 workflow를 분리한다.
- `backend/app/library/tools/run_meeting_context_retrieval.py`: `make_run_meeting_context_retrieval(path)`가 `@tool("run_meeting_context_retrieval")`(인자 `query: str, topics: list[str] | None`)을 돌려준다. Agent에게 보이는 표면(이름·설명·schema)만 가지며, 어느 데이터를 읽는지는 호출한 system이 `path`로 정한다 (`systems/k_electronics/tools/run_meeting_context_retrieval.py`가 `data/meetings.json`에 바인딩).
- `backend/app/library/workflows/meeting_context_retrieval.py`: 내부 구현 workflow. 순수 함수 `retrieve_meeting_context(query, topics, path=...)`가 주어진 회의 store(K: `systems/k_electronics/data/meetings.json`)를 키워드 점수(topic 3점, title 2점, decision 1점, 절대/상대 threshold)로 조회한다. LLM 없이 테스트한다.
- 반환은 dict (`matched`, `meetings[]`에 `decisions`, `open_action_items`).
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
- Delegation: K Super에 허용된 Tool `delegate_to_meeting_agent` / `delegate_to_news_agent` / `delegate_to_advisory_agent`(`capabilities/tools/`)가 같은 프로세스 안에서 `run_meeting_agent(task)` / `run_news_agent(task)` / `run_advisory_agent(task)`를 직접 호출한다.
- A2A (Phase 5a): Group system의 Tool `request_k_electronics_review`(`systems/group/tools/`)가 `runtime/a2a.request_review()`로 K의 `POST /api/a2a/run`을 HTTP 호출한다.
  `a2a_start(group_super → k_super, request)` emit → K의 NDJSON 스트림을 읽으며 K의 lifecycle 이벤트를 `relayed: true`로 자기 run에 다시 emit(관찰용) → 마지막 `agent_end`의 `response`를 꺼내 `a2a_end` emit 후 반환.
  Group Super의 `ToolMessage`에는 그 최종 텍스트만 들어간다. K의 `error`는 `AgentError("k_super", ...)`로 전파되어 Group run이 중단된다. 연결 실패도 같다.
  K의 주소는 `K_ELECTRONICS_A2A_URL`(기본 `http://localhost:8000/api/a2a/run`). 테스트는 `make_client`를 K app의 TestClient로 바꿔 프로세스 안에서 같은 경로를 돈다.
  frontend는 아직 이 경로를 쓰지 않는다 (Step 2·7은 mock, Phase 7에서 교체).

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
| `run_meeting_context_retrieval` | tool | `meeting` | `systems/k_electronics/tools/run_meeting_context_retrieval.py` = `library/tools/...make_run_meeting_context_retrieval(data/meetings.json)` → `library/workflows/meeting_context_retrieval.retrieve_meeting_context` |
| `external_risk_analysis` | skill | `news` | `library/skills/external_risk_analysis.py` |
| `run_company_risk_news_analysis` | tool | `news` | `systems/k_electronics/tools/run_company_risk_news_analysis.py` = `make_run_company_risk_news_analysis(data/news.json)` → `library/workflows/company_risk_news_analysis.analyze_company_risk_news` |
| `mna_feasibility_review` | skill | `advisory` | `library/skills/mna_feasibility_review.py` |
| `run_mna_review` | tool | `advisory` | `systems/k_electronics/tools/run_mna_review.py` = `make_run_mna_review(data/companies.json)` → `library/workflows/mna_review.review_acquisition` |

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
- `AgentError(agent, message)`로 실패한 Agent를 지목한다. 지목되지 않은 예외는 `k_super`로 귀속된다.

| 이벤트 | payload | 발생 위치 |
| --- | --- | --- |
| `agent_start` | `agent, request` | `agent_core.run_super`(K Super / Group Super), `agent_core.run_specialist`(Meeting / News / Advisory) 시작 |
| `a2a_start` | `from, to, request` | Group의 A2A client Tool 진입 (Phase 5a) |
| `a2a_end` | `from, to, response` | 위 Tool 반환 직전. 그 사이의 K 이벤트는 `relayed: true`로 relay된다 |
| `delegation_start` | `from, to, task` | `delegate_to_*` 진입 |
| `tool_start` | `agent, tool, args` | `common.execute_tool_calls` (Specialist의 Workflow Tool) |
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
| `POST /api/a2a/run` `{request, trace_id?, caller?}` (`/api/k-super/run`은 같은 handler의 옛 경로) | **A2A 수신 표면.** 다른 system의 Super Agent가 보낸 요청으로 K Super 실행(`invocation="a2a"`, 필요한 전문 Agent에게 순차 위임 후 최종 검토 결과 작성). lifecycle을 NDJSON으로 스트리밍하고 `agent_end(k_super).response`가 A2A 응답이다. `trace_id` · `caller`는 이벤트 stamp로만 쓰인다 (업무 payload 아님). 현재 frontend scenario(Step 3~6)가 `/api/k-super/run`을 쓴다 |
| `POST /api/agents/k-super/run` `{request}` | 사용자 → K Super 직접 대화 (`invocation="direct"`, 위임 없이 답해도 된다). 같은 Agent core |

`backend/app/systems/group/app.py` (FastAPI, `uvicorn app.systems.group.app:app --port 8001`)

| Endpoint | 역할 |
| --- | --- |
| `GET /api/health` | 상태 확인 (`system: group`) |
| `GET /api/events` | 관찰 채널 (relay된 K 이벤트 포함) |
| `POST /api/agents/group-super/run` `{request}` | 사용자 → Group Super 직접 대화. 계열사 검토가 필요하면 A2A Tool로 K에 요청하고 그룹 판단을 쓴다. NDJSON 스트림에 `a2a_start` · relayed K 이벤트 · `a2a_end` · `agent_end(group_super)`가 흐른다 |
| `POST /api/agents/meeting/run` `{request}` | 사용자 → Meeting Agent 직접 대화 (`invocation="direct"`). 같은 Agent core |
| `POST /api/agents/news/run` `{request}` | 사용자 → News Agent 직접 대화. 같은 Agent core |
| `POST /api/agents/advisory/run` `{request}` | 사용자 → Advisory Agent 직접 대화. 같은 Agent core |

### Current implementation — frontend 반영

- `src/live/api.ts` — NDJSON 스트림 클라이언트. `error` 이벤트는 `LiveRunError(agent, message)`로 던진다.
- `src/live/agentEvents.ts` — backend 이벤트 → 기존 `ScenarioEvent`로 변환. 예약·추측 없이 도착한 이벤트만 상태로 바꾼다.
  Workflow 결과 렌더링(`toolResultBlocks`)은 Tool 이름별 `switch`(meeting, news, advisory)이고, 모르는 Tool은 JSON으로 표시한다.
  `run_mna_review`는 persona 배지 · 입력 정보 · persona 우선순위대로의 평가 항목 · 권고 배지 · 조건 목록으로 그린다. 값은 전부 backend 결과이며 frontend가 만들지 않는다.
  한 K Super 실행에 위임이 여러 번 있어도 같은 변환이 반복된다. `delegation_end`의 `session_close`에는 UI 전용 `delay`가 있어 다음 세션 창이 열리기 전에 이전 창이 접힌다.
- `src/scenario/scenarioEvents.ts` — Step 3·4·5·6은 `live` 이벤트 하나다. Step 번호는 UI 개념으로만 남기되, backend `delegation_start`의 대상 Agent에 따라
  `LIVE_DELEGATION_STEPS`(meeting → 3, news → 4, advisory → 5)로, K Super의 `agent_end`에 `LIVE_SYNTHESIS_STEP`(6)으로 Step 표시를 올린다. backend는 Step을 모른다.
  `done`이 오면 `live` 이벤트가 끝나고 Step 7 mock(A2A 응답)이 이어진다.
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
| `agent_end`(k_super) | Step 6 · 열린 A2A 세션에 "K전자 검토 결과" 메시지(실제 `response` 문자열 그대로), K전자 status "검토 결과 작성 완료". 노드 completed는 Step 7 mock(A2A 응답)이 담당 |
| `error` | 해당 노드 error, 오류 메시지, scenario `error` 상태 |

직접 대화(Sidebar "미팅" · "뉴스" · "경영자문" 탭)는 `App.tsx`가 `LIVE_AGENT_PATHS` + `runDirectLive` + 같은 mapper로 처리한다. 전문 Agent 탭에 mock 응답 경로는 없다.
직접 대화에서 Tool 없이 답하면 `tool_start`가 없으므로 노드에 Workflow 이름이 표시되지 않고 응답 메시지만 붙는다.

---

## 7. 개발 전략: mock → real vertical slice

전체 시스템을 한 번에 구현하지 않는다.
이미 만들어진 deterministic 8-step demo를 **한 Step씩 실제 실행으로 교체**한다.

```text
지금            다음              그 다음
Step 1 MOCK     Step 1 MOCK       ...
Step 2 MOCK     Step 2 MOCK
Step 3 REAL     Step 3 REAL
Step 4 REAL     Step 4 REAL
Step 5 REAL     Step 5 REAL
Step 6 REAL     Step 6 REAL
Step 7 MOCK  →  Step 7 REAL
...
```

- 기존 UI와 scenario shell(`scenarioEvents.ts`, AgentGraph, Session UI)은 최대한 유지하고 내부 실행부만 실제 Agent로 바꾼다.
- 한 Step을 real로 바꿀 때 그 Step의 mock 이벤트를 `live` 이벤트로 치환하고, 나머지 Step은 그대로 둔다.
- mock Step은 **의도된 임시 상태**다. 교체 전까지는 frontend 상수(`mockData.ts`의 `COMPANY`)와 `scenarioEvents.ts`의 고정 문장으로 재생된다. 이것이 6장 원칙에 아직 미치지 못하는 부분이며, Step이 real이 될수록 해소된다.
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
| 4 | K Super → News Agent → `run_company_risk_news_analysis` | **REAL** | 같은 K Super 실행 안에서 Meeting 결과 수신 후 순차 위임 |
| 5 | K Super → Advisory Agent → `run_mna_review` | **REAL** | 같은 K Super 실행 안에서 Meeting · News 결과를 보고 맥락을 담아 순차 위임 |
| 6 | K Super가 전문 Agent 결과 종합 → K전자 차원의 최종 검토 결과 | **REAL** | 같은 K Super 실행의 마지막 `model` 턴. 추가 위임이 필요 없다고 판단해 Tool 호출 없이 응답 → `agent_end(k_super)`. REAL 구간 종료 |
| 7 | K Super → Group Super. A2A 응답 | **MOCK** · 다음 구현 대상 | frontend 이벤트 (A2A 세션 닫힘). 결과 문장은 아직 mock 고정 문장 |
| 8 | Group Super → 회장. 그룹 투자심의 최종 판단 | **MOCK** | frontend 이벤트 |

직접 대화 탭:

| Sidebar 탭 | 상태 |
| --- | --- |
| 그룹 슈퍼에이전트 | scenario 트리거. 비매칭 입력은 고정 안내문 |
| 미팅 | **REAL** (`POST /api/agents/meeting/run`) |
| 뉴스 | **REAL** (`POST /api/agents/news/run`) |
| 경영자문 | **REAL** (`POST /api/agents/advisory/run`) |

K Electronics Super는 Sidebar에 없고 그래프에서만 보인다.

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
| frontend가 Step 3 진입 | `scenarioEvents.ts`의 `type: "live"` 이벤트가 `streamAgentRun(K_SUPER_RUN_PATH, A2A_REQUEST_TEXT)` 호출 |
| API | `systems/k_electronics/app.py` `k_super_run` → `stream_run(lambda: run_k_super(request), run=new_run(system="k_electronics", root_agent="k_super", origin="a2a"))` |
| K Super LLM 판단 | `agent_core.build_super_graph`의 `call_model`: `SystemMessage(SPEC.system_prompt + 호출 맥락)` + `bind_tools(SPEC.tools)`. 둘 다 `k_super.py`가 Registry에서 `skills_for` / `tools_for("k_super")`로 받은 것 |
| 위임 | `agent_core.build_super_graph`의 `run_tools` → `systems/k_electronics/tools/delegate_to_*.invoke` → `delegation_start` emit → `run_*_agent(task)` |
| 전문 Agent LLM 판단 | `agent_core.build_specialist_graph`의 `call_model` (각 Agent 모듈이 Registry에서 받은 `SYSTEM_PROMPT`, `TOOLS` + 호출 맥락 문단) |
| Workflow Tool 실행 | `agent_core.execute_tool_calls` → `tool_start` emit → `systems/k_electronics/tools/run_*`(library factory instance).invoke → `library/workflows/*` → `tool_end` emit |
| 응답 | `agent_core.run_specialist`: delegation이면 Tool 호출 검사 → `final_response` → `agent_end` emit → 반환 → `delegation_end` emit → `ToolMessage` |
| 다음 위임 / 종료 | `agent_core.build_super_graph`: `tools → model` 고정 엣지. 다음 `model` 턴이 Tool을 호출하면 `route_on_tool_calls` → `tools`, 아니면 → `END` |
| 종합 (Step 6) | 마지막 `model` 턴의 AI 응답. `agent_core.run_super`가 근거 검사(`a2a`: 위임 1회 이상, 응답 비어 있지 않음) 후 `agent_end(k_super, response)` emit |
| UI 반영 | `agentEvents.ts` mapper → `useScenarioRunner` reducer. `delegation_start`의 대상에 따라 Step 3 / 4 / 5, K Super `agent_end`에 Step 6 표시 |

LLM: `backend/app/runtime/llm.py` — `ChatOllama`, Ollama Cloud(`https://ollama.com`), 기본 모델 `deepseek-v4.1-flash`, `OLLAMA_API_KEY` 필요. 키는 `backend/.env`에 두고 Git에 넣지 않는다.

테스트:

| 파일 | LLM 필요 | 검증 내용 |
| --- | --- | --- |
| `backend/tests/test_workflow.py` | 아니오 | 회의 조회 workflow |
| `backend/tests/test_news_workflow.py` | 아니오 | 뉴스 리스크 workflow (기업 alias, category 필터, 정렬) |
| `backend/tests/test_mna_review_workflow.py` | 아니오 | M&A 검토 workflow (persona별 항목 순서 · 우려 · 조건 순서, 같은 권고와 조건 집합, context 신호, 모르는 기업/persona) |
| `backend/tests/test_capabilities.py` | 아니오 | Registry 내용(kind, allowed_agents), Tool 정의가 source of truth, Agent가 Registry subset을 쓰는지, Skill이 system prompt에 들어가는지 |
| `backend/tests/test_k_super.py` | 아니오 | `route_on_tool_calls`(tool call → tools, 일반 응답 → END), `tools → model` 고정 엣지, scripted model(`common.get_chat_model` patch) + stub 위임 Tool로 만든 그래프(`k_super.build_k_super` patch)로 `run_k_super`: 일부 전문 Agent만 쓰고 답해도 정상 종료 · 위임 없는 응답과 빈 응답은 `AgentError` |
| `backend/tests/test_events.py` | 아니오 | emit / collect / stream / error 귀속(root_agent) / run 식별자 stamp(relay된 stamp 유지) / HUB publish·subscribe·keep-alive / SSE 형식 / `.env` 경로 |
| `backend/tests/test_k_app.py` | 아니오 | K app 표면을 scripted model로 end-to-end: `/api/a2a/run`이 K Super → Meeting → Tool 체인을 돌리고 trace_id · caller · origin을 stamp, 위임 없는 답은 `error`, 옛 경로 동일, `/api/agents/k-super/run`은 위임 없이 답해도 정상 |
| `backend/tests/test_group.py` | 아니오 | Group Registry 내용, A2A Tool이 K lifecycle을 relay하고 최종 응답만 반환, K 실패·연결 실패를 `k_super`로 귀속, Group app → K app 프로세스 내 end-to-end(stamp · trace · relayed 검증), 직접 인사 |
| `backend/tests/test_boundaries.py` | 아니오 | import 경계: runtime은 library·systems를, library는 systems를, system은 다른 system을 import하지 않음. system마다 registry.py와 data/ 존재 |
| `backend/tests/test_agents.py` | 예 (`OLLAMA_API_KEY` 없으면 skip) | Meeting / News / Advisory Agent 단독 실행, K Super 직접 대화(인사에는 위임 없이 · 회의 기준 요청에는 Meeting 위임), Group Super → K A2A 실제 왕복(relay된 위임 세 건, 그룹 판단) · Group 직접 인사, K Super가 NeuroEdge 요청에 Meeting → News → Advisory를 각각 별도 model 턴에서 순차 위임하고 Advisory task에 회의 기준·외부 리스크를 담았는지(`context_signals`), 마지막에 Tool 호출 없는 최종 응답(본실사 판단 포함)으로 끝나는지, 회의 결정사항만 묻는 요청에는 Meeting에게만 위임하고 답하는지, NDJSON API, 경영자문 직접 대화의 개념 질문(Tool 없음) / 실제 검토(Tool 사용), delegation 경로의 Tool 검사 |

---

## 10. 현재 구현과 목표 구조의 차이

| 영역 | Current implementation | Target architecture |
| --- | --- | --- |
| Capability 정의 | 각 system의 `registry.py`에서 Agent별 Skill / Tool subset 제공. frontend에 Workflow 이름 상수 없음 | 같음 |
| K Super 그래프 | `tools → model` 루프. 모델이 Tool 없이 응답하면 `END`, 그 응답이 종합 결과 | 같음 |
| Group Super | backend REAL (Group system, Phase 5a). frontend는 mock | 같음 + frontend 연결 (Phase 7) |
| A2A | backend REAL: Group Tool → K `/api/a2a/run` HTTP, 최종 응답만 Agent에 전달, lifecycle은 관찰용 relay. frontend scenario는 아직 상수 요청문을 K에 직접 전달 | frontend가 Group backend를 쓰면 완성 (Phase 7) |
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
| K UI | 제품 | 탭 4개(K Super · 경영자문 · 뉴스 · 미팅) + 협업 세션창. 그래프 없음 | K backend의 요청자 스트림 (외부 A2A 실행 표시는 미확정, 넣으면 관찰 채널) |
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

1. **A2A 프로토콜.** Group Super의 Capability Tool(K전자 검토 요청)이 K backend의 A2A 수신 엔드포인트를 HTTP로 호출한다. K는 현재 `/api/k-super/run`과 같은 NDJSON lifecycle 스트림을 돌려주고 `agent_end(k_super).response`가 최종 응답이다.
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
11. **미확정.** K UI에서 외부 A2A로 시작된 실행의 표시(없음 / 작은 상태 badge / 읽기 전용 수신 기록). 침투적인 자동 UI 전환은 하지 않는다.

### 전환 단계와 상태

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0 | 결정 문서화 (이 장) | 완료 |
| 1 | 동작 변화 없는 runtime 정비: Super core 추출(invocation) · run context · Registry 메커니즘/내용 분리 · workflow 데이터 경로 매개변수 | 완료 |
| 2 | 디렉터리 경계 이동(runtime / 라이브러리 / K system) + import 경계 테스트 | 완료 |
| 3 | 관찰 hub + SSE (별도 run 시작 이벤트는 두지 않음: root Agent의 `agent_start`에 `origin` · `caller`가 찍힌다) | 완료 |
| 4 | K Super의 두 표면: A2A 수신(`a2a`) · 직접 채팅(`direct`) | 완료 |
| 5a | Group system: Group Super + A2A client Tool + Group app | 완료 |
| 5b | Group Specialist 3개 + Group 데이터 + Group Super의 `delegate_to_*` | 예정 |
| 6 | Demo Monitor | 예정 |
| 7 | 제품 UI(Group · K), mock scenario 제거 | 예정 |
| 8 | 정리 · 문서 · (선택) K UI 외부 실행 표시 | 예정 |

Phase 7 전까지 기존 단일 frontend와 mock Step 1·2·7·8은 그대로 두며, 각 Phase가 끝날 때 현재 데모가 그대로 동작해야 한다.

---

## 12. Current limitations / Next architectural work

구현 전에 결정이 필요한 지점이다. 코드에서 조용히 확정하지 말고 명시적으로 결정한다.

1. **Scenario Step 경계 (Step 4에서 내린 결정).** Step은 **UI 전용** 개념으로 유지한다. backend에는 Step이 없다.
   K Super 실행 한 번이 Step 3·4·5를 포함하므로 `scenarioEvents.ts`의 `LIVE_DELEGATION_STEPS`가 backend `delegation_start`의 대상 Agent를 Step 번호로 매핑한다 (meeting → 3, news → 4, advisory → 5).
   종합(Step 6)은 위임이 아니므로 K Super의 `agent_end`(`response` 있음)로 올린다 (Step 6에서 내린 결정, `LIVE_SYNTHESIS_STEP`).
   Step 7·8은 여전히 mock 이벤트가 Step을 올린다. **2026-10-12 결정**: Step 표시는 제품 UI에서 빠지고 Demo Monitor로 옮긴다 ([11장](#11-목표-구조와-전환-계획-2026-10-12-결정) 8번).
2. **K Super 위임 정책 (Step 4에서 내린 결정과 남은 부분).** `tools → model` 루프로 바꿨고, 위임은 **순차**다: Skill이 "한 번에 하나"를 지시하고 `tests/test_agents.py`가 각 위임이 별도 model 턴에서 일어남을 검증한다.
   모델이 한 턴에 여러 tool call을 보내면 `run_tools`는 그대로 순차 실행한다 (거부하지 않음). 이벤트 순서는 그래도 순차다.
   **종료 조건 (Step 6에서 내린 결정)**: "허용된 위임 Tool을 모두 호출"하는 임시 규칙(`_after_tools`, `pending_delegations`)을 제거했다.
   매 `model` 턴이 다음 행동을 정하고, Tool 호출 없는 응답이 곧 종료다. 어떤 전문 Agent를 쓸지는 Skill `specialist_delegation`의 지침(요청의 항목 ↔ 위임 Tool 설명)에 따른 모델 판단이며,
   `tests/test_agents.py`가 NeuroEdge 요청(세 Agent 모두)과 회의 결정사항만 묻는 요청(Meeting만)으로 확인한다. 모델이 필요한 Agent를 빠뜨리거나 불필요한 Agent를 부르는 것을 막는 코드는 없다 (의도적).
   병렬 위임과 frontend session stack(중첩 전제)의 형제 세션 처리는 여전히 열려 있다.
3. **위임 Tool ↔ 대상 Agent의 순환 의존 (Step 5에서 내린 결정과 남은 부분).** `delegate_to_*` Tool은 대상 Agent의 `run_*`를 호출 시점에 import해서 순환을 끊는다 ([4장](#4-capability-registry)).
   Step 5에서 **공통 specialist core**, Phase 1에서 **공통 super core**를 도입했다 (`common.SuperAgent` / `build_super_graph` / `run_super`, [3.1](#31-super-agent)). Agent 파일은 정체성과 Registry 조회만 갖는다.
   위임 Tool 3개는 여전히 같은 모양의 복사본이다. 각 Tool의 docstring이 K Super에게 보이는 담당 설명(Agent-visible 표면)이라 파일을 따로 두는 쪽이 읽기 쉽고, "agent id → runner" 테이블은 아직 도입하지 않았다. 네 번째 전문 Agent가 생기면 다시 본다.
   `tests/test_agents.py`의 `_NoToolGraph`처럼 `build_*_agent`를 monkeypatch하는 테스트가 있으므로 `run_*_agent`는 그래프를 모듈 전역 이름으로 조회한다.
4. **Workflow 결과의 frontend 렌더링.** Tool별 `switch`를 계속 늘릴지, backend가 표시 힌트를 함께 보낼지, 범용 렌더로 갈지.
5. **상위 Agent로 전달할 구조화 결과 (Step 5에서 내린 결정과 남은 부분).** Step 5에서는 공유 구조를 만들지 않았다. Advisory가 필요로 하는 Meeting / News 결과는 K Super가 자기 history를 보고 **자연어 위임 문장에 직접 담는다** (Skill `specialist_delegation`의 지침, 코드 template 없음).
   Advisory Agent는 그 문장을 `run_mna_review`의 `context`(자유 텍스트)로 넘기고 workflow가 신호를 추출한다. 따라서 K Super가 무엇을 옮겨 적는지, Advisory가 무엇을 `context`에 넣는지는 모델 판단이며 실행마다 다를 수 있다 (`tests/test_agents.py`가 가격 기준 · 리스크 주제가 전달됐는지만 검증).
   Step 6 종합은 **텍스트만으로 구현했다**: K Super는 history의 `ToolMessage` 텍스트를 보고 최종 응답을 쓰고, frontend는 그 문자열을 그대로 표시한다.
   Step 7·8(Group Super가 K Super 응답을 받아 판단)에서 구조화된 결과(권고 코드, 조건 목록 등)가 필요해지면 `artifacts` / `context` 같은 공유 구조를 다시 본다.
6. **Group Super와 A2A (2026-10-12에 결정).** 별도 프로세스(Group backend)의 Group Super가 Capability Tool로 K backend의 A2A 수신 표면을 HTTP 호출한다. 상세는 [11장](#11-목표-구조와-전환-계획-2026-10-12-결정). 구현 전까지 Current implementation은 그대로(backend에 Group Super 없음).
7. **오류 귀속.** `stream_run`이 `AgentError` 외 예외를 run의 `root_agent`로 귀속한다 (Phase 1). 실행 중인 하위 Agent까지 정확히 지목하려면 실행 중 Agent 추적이 필요하다 (미해결).
8. **Agent 식별자 동기화.** backend `AGENT_ID` 문자열과 frontend `AgentId`가 수동으로 일치해야 한다. frontend는 모르는 id의 이벤트를 조용히 버린다. `LIVE_DELEGATION_STEPS`(frontend)도 같은 id를 쓴다.
   backend 안에서도 `systems/k_electronics/registry.py`(`allowed_agents`)와 `systems/k_electronics/tools/delegate_to_*.py`(`from` / `to`)가 같은 문자열을 리터럴로 가진다. Registry가 Agent 모듈을 import할 수 없기 때문이며, `tests/test_capabilities.py`가 일치를 검증한다.
   **2026-10-12 결정**: 두 system이 같은 Specialist id를 쓰므로 id는 system 내부 이름으로 두고 runtime이 이벤트에 `system` 태그를 붙인다 ([11장](#11-목표-구조와-전환-계획-2026-10-12-결정) 3번).
9. **Advisory persona 선택과 다중 관점.** persona는 `run_mna_review`의 인자이고 어떤 관점을 쓸지는 Advisory Agent의 모델 판단이다 (Skill: 한 번의 검토에 관점 하나, 지정 없으면 CEO).
   초기 실행에서 K Super가 "CEO/CFO/CTO 관점"을 모두 요구해 Advisory가 Tool을 세 번 호출한 사례가 있어 Skill과 위임 Tool 설명을 "관점 하나"로 조였다. 여러 관점의 비교 · 토론(CFO vs CTO)은 범위 밖이며, 필요해지면 Tool을 여러 번 호출할지, workflow가 다중 persona를 한 번에 돌려줄지 결정한다.
10. **Advisory 호출 맥락의 2중 전달.** K Super → Advisory는 `task` 문장, Advisory → Tool은 `context` 인자로 같은 맥락이 두 번 LLM을 거친다. Advisory가 `context`를 요약하거나 빠뜨리면 workflow의 신호 추출이 약해진다 (Skill이 "그대로 넣으라"고 지시). 공유 구조(5번)를 도입하면 함께 해소된다.

---

## 13. 이 문서의 유지

- Step이 real로 바뀌거나 architecture 결정이 내려지면 이 문서의 해당 표를 같은 커밋에서 갱신한다.
- "Current implementation"에는 코드에 있는 것만 적는다. 계획은 "Target architecture" 또는 12장에 적는다.
- 실행 방법과 파일별 상세는 `README.md`에 있다. 개발 규칙은 `AGENTS.md`, Claude 전용 지침은 `CLAUDE.md`다.
