# Executive AI — Multi-Agent Decision Support Demo

대기업 경영진을 위한 Multi-Agent AI 시스템 데모입니다.
고정된 NeuroEdge M&A 8 Step 시나리오를 재생하면서
`Group Super → A2A → K Electronics Super → Agent Delegation → Workflow Tool → 결과 종합`
구조를 채팅과 그래프에서 동시에 보여줍니다.

mock을 한 단계씩 실제 Agent 실행으로 교체하는 중입니다. 현재 상태:

```text
Step 1  MOCK   회장 요청 접수
Step 2  MOCK   A2A 요청 (Group Super → K Super)
Step 3  REAL   K Super → Meeting Agent → run_meeting_context_retrieval   (Python · LangGraph · Ollama Cloud, 실행 이벤트 스트리밍)
Step 4  REAL   K Super → News Agent → run_company_risk_news_analysis     (같은 K Super 실행 안에서 Meeting 결과 수신 후 순차 위임)
Step 5  REAL   K Super → Advisory Agent → run_mna_review                 (Meeting · News 결과를 K Super가 맥락으로 담아 위임, CEO/CFO/CTO 관점 평가)
Step 6  REAL   K Super 종합                                              (추가 위임이 필요 없다고 판단하면 Tool 호출 없이 최종 검토 결과를 작성 → agent_end)
Step 7  MOCK   A2A 응답
Step 8  MOCK   그룹 최종 판단
```

Sidebar **미팅** · **뉴스** · **경영자문** 탭의 직접 채팅도 같은 Meeting / News / Advisory Agent가 실제로 응답합니다.
직접 채팅에서는 Tool 사용이 강제되지 않아, 일반 개념 질문에는 Tool 없이 답하고 실제 기업 검토 요청에는 Workflow Tool을 사용합니다.

## 실행

```bash
# 1) Python backend (별도 터미널)
cd backend
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # OLLAMA_API_KEY 입력 (https://ollama.com/settings/keys)
.venv/bin/uvicorn app.main:app --port 8000 --reload

# 2) React frontend
npm install
npm run dev      # http://localhost:5173  (/api → localhost:8000 proxy)
npm run build    # tsc + vite build
```

backend 테스트:

```bash
cd backend
.venv/bin/python -m pytest        # tests/test_agents.py는 OLLAMA_API_KEY가 없으면 skip
```

Step 3·4·5·6의 그래프/세션/메시지는 backend가 실행 중 보내는 lifecycle 이벤트(NDJSON 스트림)를 받는 즉시 바뀝니다.
Step 번호는 backend `delegation_start`의 대상 Agent(meeting → 3, news → 4, advisory → 5)와 K Super의 `agent_end`(→ 6)에 맞춰 바뀝니다.
K Super가 어떤 전문 Agent에게 위임할지, 언제 위임을 멈추고 최종 검토 결과를 쓸지는 요청과 누적된 결과를 보고 매 턴 스스로 판단합니다
(등록된 Tool을 모두 호출해야 하는 규칙은 없습니다). Step 6 메시지("K전자 검토 결과")는 backend `agent_end(k_super).response` 문자열 그대로입니다.
backend가 꺼져 있거나 Agent가 실패하면(LLM 오류, 전문 Agent가 Workflow Tool을 호출하지 않음, K Super가 전문 Agent에게 전혀 위임하지 않고 답함 등) 그 시점에
시나리오가 **오류 · 중단** 상태로 멈추고, backend가 지목한 Agent 노드가 error로 표시됩니다 (mock으로 대체하지 않음).
초기화 또는 새 요청으로 재실행합니다.

## 시연 순서

1. Sidebar에서 **Group Super Agent**를 선택합니다 (기본 선택).
2. `NeuroEdge` 또는 `인수` 키워드가 포함된 요청을 보냅니다.
   (빈 채팅의 **예시 요청 불러오기** 버튼으로 대표 문장을 입력할 수 있습니다.)
3. 8 Step 시나리오가 자동 재생되며 오른쪽 패널이 채팅과 동기화됩니다.
   - **Agent Graph**: 팀 회의용 토폴로지 시각화 (실제 제품 UI에는 포함되지 않음)
   - **Agent Sessions**: 세션마다 별도 채팅창이 옆에 열리는 실제 대화 뷰.
     A2A 요청 시 2번째 창(Group Super ↔ K Electronics Super), Delegation 시 3번째 창
     (K Electronics Super ↔ 전문 Agent)이 생기고, 세션이 끝나면 창이 부모 창의 요청 말풍선으로
     날아 들어가며 사라집니다. 남은 요청 카드에는 결과가 붙고 "대화 보기"로 다시 펼칠 수 있습니다.
4. 완료 후 Sidebar의 **뉴스** 탭에서 `뉴로엣지 관련 악재만 다시 보여줘.`를 보내면
   같은 News Agent(backend)가 사용자 직접 요청에도 실제로 응답하며, 그래프의 News 노드가 다시 active 됩니다.
   **경영자문** 탭에서 `뉴로엣지 인수를 CFO 관점에서 검토해줘.`를 보내면 같은 Advisory Agent가 `run_mna_review(persona="CFO")`로 답하고,
   `earn-out이 뭐야?` 같은 개념 질문에는 Tool 없이 답합니다.
5. **Reset Scenario**로 초기화하고 재실행할 수 있습니다.

## 아키텍처 개념 (UI 표현)

| 개념                   | 의미                                               | UI                                    |
| ---------------------- | -------------------------------------------------- | ------------------------------------- |
| Super Agent            | 요청 이해 · 위임 · 결과 종합 (orchestration)       | indigo 노드                           |
| A2A                    | 독립 Super Agent 시스템 간 통신                    | 굵은 indigo edge, `A2A` 라벨          |
| Agent Delegation       | Super Agent → 자신의 전문 Agent 자연어 Task 위임   | teal 점선 edge, `Delegate` 라벨       |
| Workflow Tool          | 전문 Agent가 Skill에 따라 선택·실행하는 정형 업무  | 노드 안 `run_*` 모노스페이스 라벨     |
| Workflow 내부 단계     | Tool 내부에 캡슐화 (Agent가 단계별 호출하지 않음)  | 표시하지 않음 (`tool_end` 결과만)     |

그래프 노드 6개: Group Chairman, Group Super Agent, K Electronics Super Agent,
Meeting Agent, News Agent, Management Advisory. K Electronics Super는 Sidebar에 없고
그래프에서만 보입니다.

## 구조

```text
src/
  types/
    agent.ts           AgentId · 상태 · CommunicationType(a2a | delegation) · 토폴로지 · 좌표
    scenario.ts        ScenarioEvent(live 포함) · ChatItem · MessageBlock · RunnerState
  scenario/
    mockData.ts        남은 mock Step(1–2, 7–8)용 고정 데이터 (기업명)
    scenarioEvents.ts  8 Step M&A 시나리오 이벤트 배열 (Step 3·4·5·6은 `live` 이벤트 하나, 나머지는 mock)
    mnaScenario.ts     시나리오 메타 + 트리거 조건 + 예시 문장
    mockResponses.ts   Group Super 탭의 비시나리오 입력 안내문
  live/
    api.ts             backend NDJSON 스트림 클라이언트 (POST /api/k-super/run, /api/agents/{meeting,news,advisory}/run)
    agentEvents.ts     backend lifecycle 이벤트 → ScenarioEvent 변환 (Workflow 결과 블록: meeting / news / mna_review, K Super 최종 응답 메시지)
  hooks/
    useScenarioRunner.ts  이벤트 재생 → 노드/엣지/채팅 상태. `live` 이벤트 실행 중 도착하는 이벤트를 즉시 적용.
                          runDirectLive()로 전문 Agent 단독 실행(실제 backend), apply()로 단일 이벤트 적용
    useCollapsePhase.ts   open → collapsing → closed 전환 (창/카드 접힘 애니메이션용)
  components/
    Header, Sidebar, ChatPanel, ChatMessage, ActivityCard, ComparisonCard(미사용),
    AgentGraph (React Flow), AgentNode, ScenarioStatus, PanelSwitch,
    SessionCard (세션을 연 요청 카드 + 결과/재생), SessionWindow (세션별 채팅창),
    SessionWindows (열린 세션 창 목록)
  App.tsx

backend/
  app/
    main.py                          `uvicorn app.main:app` 진입점. K Electronics system의 app을 재노출
    runtime/                         공통 runtime (Agent id · Capability 내용 · 데이터를 모름)
      agent_core.py                  Specialist core(Specialist / build_specialist_graph / run_specialist, 호출 맥락 delegation|direct)
                                     + Super core(SuperAgent / build_super_graph / run_super, 호출 맥락 a2a|direct), system prompt 조립, 응답 추출
      capabilities.py                Skill / Capability 타입 + Registry 조회·검증 메커니즘
      events.py                      emit() / RunContext(run_id · system · origin · trace_id · caller stamp) / stream_run() / EventHub(HUB: 프로세스 전역 관찰 fan-out), AgentError
      llm.py                         ChatOllama (Ollama Cloud, OLLAMA_* 환경변수)
      schemas.py                     요청 스키마
      api.py                         NDJSON 스트리밍 helper · SSE 관찰 채널 helper
    library/                         공용 Capability 라이브러리 (system을 import하지 않음)
      skills/                        specialist_delegation, meeting_context_analysis, external_risk_analysis, mna_feasibility_review (Skill 본문)
      tools/                         run_meeting_context_retrieval, run_company_risk_news_analysis, run_mna_review — make_<tool>(path) factory
                                     (Tool 이름 · 설명 · schema의 source of truth)
      workflows/
        meeting_context_retrieval.py Tool 내부 구현: 회의 store(path) 키워드 점수 조회 (LangChain 의존 없음)
        company_risk_news_analysis.py 기업 alias + 리스크 category 키워드로 기사 store(path) 조회
        mna_review.py                기업 store(path) + persona(CEO/CFO/CTO)별 평가 기준 + context 신호 → 항목별 평가 · 권고 · 조건
    systems/
      k_electronics/                 K Electronics agent system (SYSTEM = "k_electronics", DATA_DIR)
        app.py                       FastAPI: /api/health, /api/a2a/run (= /api/k-super/run, A2A 수신), /api/agents/{k-super,meeting,news,advisory}/run (직접 채팅; NDJSON 스트림), /api/events (SSE 관찰 채널)
        registry.py                  이 system의 CAPABILITIES와 allowed_agents (권한의 source of truth), skills_for / tools_for
        agents/
          k_super.py                 K전자 슈퍼에이전트: AGENT_ID · IDENTITY + Registry 위임 Tool (Super core)
          meeting.py                 미팅 에이전트: AGENT_ID · IDENTITY + Registry Skill/Tool(run_meeting_context_retrieval)
          news.py                    뉴스 에이전트: AGENT_ID · IDENTITY + Registry Skill/Tool(run_company_risk_news_analysis)
          advisory.py                경영자문 에이전트: AGENT_ID · IDENTITY + Registry Skill/Tool(run_mna_review)
        tools/
          delegate_to_*.py           K Super → 미팅 / 뉴스 / 경영자문 위임 Tool (delegation_start/end 발행, 최종 응답만 반환)
          run_*.py                   라이브러리 Tool factory를 이 system의 data/에 바인딩한 instance
        data/meetings.json           회의 fixture (NeuroEdge 전략회의 + 무관한 회의 2건)
        data/news.json               뉴스 fixture (NeuroEdge 리스크 기사 3건 + 무관한 기업 기사 2건)
        data/companies.json          인수 대상 기업 fixture (NeuroEdge 재무 · 가치 · 고객 · 기술 · 전략)
  tests/                             test_workflow.py · test_news_workflow.py · test_mna_review_workflow.py · test_capabilities.py · test_k_super.py · test_k_app.py · test_events.py
                                     · test_boundaries.py(runtime/library/system import 경계) (LLM 불필요), test_agents.py (Ollama Cloud 필요)
```

### Agent 구조 (backend)

```text
K전자 슈퍼에이전트 (LangGraph, MessagesState)
  model ──tool call──▶ delegate_to_meeting_agent(task)   ← LLM이 자연어 업무를 작성해 호출
                              │ invoke
                              ▼
                       미팅 에이전트 (LangGraph, MessagesState)
                         model ──tool call──▶ run_meeting_context_retrieval(query, topics)
                               ◀── tool result ──┘        (meetings.json 조회)
                         model ──▶ 최종 응답 (한국어)
                              │
                       ToolMessage(최종 응답만) ──▶ K전자 슈퍼에이전트
  model ──tool call──▶ delegate_to_news_agent(task)      ← 미팅 결과를 보고 다시 판단
                              │ invoke
                              ▼
                       뉴스 에이전트 (LangGraph, MessagesState)
                         model ──tool call──▶ run_company_risk_news_analysis(query, company, topics)
                               ◀── tool result ──┘        (news.json 조회)
                         model ──▶ 최종 응답 (한국어)
                              │
                       ToolMessage(최종 응답만) ──▶ K전자 슈퍼에이전트
  model ──tool call──▶ delegate_to_advisory_agent(task)  ← 미팅·뉴스 결과를 보고, 필요한 맥락(회의 기준·외부 리스크)을 task에 직접 써서 위임
                              │ invoke
                              ▼
                       경영자문 에이전트 (LangGraph, MessagesState)
                         model ──tool call──▶ run_mna_review(query, company, persona, context)
                               ◀── tool result ──┘        (companies.json + persona별 평가 기준 + context 신호)
                         model ──▶ 최종 응답 (한국어)
                              │
                       ToolMessage(최종 응답만) ──▶ K전자 슈퍼에이전트
  model ──▶ 요청의 항목에 대한 결과가 모두 있음 → Tool 호출 없이 최종 검토 결과 작성 (Step 6) → END
```

- 위임 대상과 횟수는 K전자 슈퍼에이전트가 요청을 보고 정합니다. 위 순서는 NeuroEdge 요청(회의 결정사항 · 외부 리스크 · 인수 타당성 · 본실사 여부)에서
  실제로 나오는 흐름이며, 코드에 고정된 것이 아닙니다. 회의 결정사항만 묻는 요청이면 미팅 에이전트 한 번 뒤 바로 답합니다.

- 각 Agent는 독립된 message history를 가집니다. 전문 에이전트 내부의 tool call / result는
  K Super의 history에 들어가지 않고, 최종 응답만 ToolMessage로 돌아갑니다.
- Agent 코드는 실제 lifecycle 시점에 `emit()`을 호출합니다. API는 Agent를 worker thread에서 돌리며
  그 이벤트를 NDJSON 한 줄씩 스트리밍하고, frontend는 받는 즉시 UI 상태로 바꿉니다.

| backend 이벤트 (실행 시점)          | UI 상태 변화                                                   |
| ----------------------------------- | -------------------------------------------------------------- |
| `agent_start` (k_super)             | K전자 노드 status "요청 분석 중..."                             |
| `delegation_start` (k_super→전문)   | Step 3/4/5 표시 · 위임 세션 열림(실제 위임 문장) · 전문 노드 active · 위임 edge active |
| `agent_start` (전문 Agent)          | 전문 노드 status "요청 분석 중..."                              |
| `tool_start`                        | 전문 노드/세션 헤더에 실제 호출된 Workflow 이름, status "실행 중" |
| `tool_end`                          | 세션에 Workflow 인자·결과 메시지                                |
| `agent_end` (전문 Agent)            | 세션에 최종 응답 메시지 · 전문 노드 completed · edge completed  |
| `delegation_end`                    | 위임 세션 닫힘 · K전자 status "결과 수신" (다음 위임이 있으면 반복) |
| `agent_end` (k_super)               | Step 6 표시 · A2A 세션에 "K전자 검토 결과" 메시지(실제 응답 문자열) · K전자 status "검토 결과 작성 완료" |
| `error` (agent 지목)                | 해당 노드 error · 오류 메시지 · 시나리오 중단                    |

- K Super 위임 경로에서 전문 Agent가 Workflow Tool을 호출하지 않고 답하면 backend가 `error`(agent=meeting | news | advisory)로 처리하므로,
  UI에 Workflow 이름이 표시되는 것은 실제 `tool_start`가 발생한 경우뿐입니다.
  Sidebar 직접 대화(`/api/agents/*/run`)는 호출 맥락이 `direct`라 이 검사를 하지 않고, Tool 사용 여부는 Agent가 Skill 지침에 따라 정합니다.
- `run_mna_review`의 persona(CEO / CFO / CTO)는 별도 Agent가 아니라 평가 관점 인자입니다. 같은 데이터면 권고와 조건 집합은 같고,
  어떤 항목을 먼저 보고 어떤 우려와 조건을 앞세우는지가 달라집니다.
- 각 Agent의 Skill(system prompt에 들어가는 업무 지침)과 Tool(`bind_tools`)은 자기 system의 `registry.py`에서
  `AGENT_ID` 기준으로 받습니다. Agent 파일은 Tool을 직접 import하지 않습니다.
- Step 7 이후: K Super의 실제 Step 6 응답을 A2A 응답으로 Group Super에게 넘기는 부분은 아직 mock입니다 (`scenarioEvents.ts`의 `session_close` 고정 문장).

### 시나리오 로직과 그래프 렌더링 분리

- `scenarioEvents.ts`에는 UI 코드가 없고, `AgentGraph.tsx`에는 시나리오 내용이 없습니다.
- `useScenarioRunner`가 이벤트를 reducer로 `nodes` / `edges` / `messages` 상태로 변환하고,
  그래프와 채팅은 그 상태만 읽습니다.
- `live` 이벤트는 실행 중 `emit`으로 들어오는 ScenarioEvent를 도착 순서대로 바로 적용합니다.
  이벤트의 `delay`는 상태 변경을 늦추지 않고 다음 이벤트만 잠시 보류합니다(결과 읽을 시간 후 세션 닫힘).
  실패하면 backend가 지목한 Agent로 `scenario_error`를 적용하고 중단합니다.

### 이벤트 타입

| type                | 역할                                                       |
| ------------------- | ---------------------------------------------------------- |
| `step`              | 진행 Step 번호 / 제목 갱신                                 |
| `live`              | 실제 Agent 실행. 실행 중 emit되는 이벤트를 즉시 적용, 실패 시 중단 |
| `agent_start`       | 노드 active (+ `workflow` 표시) + from이 있으면 edge active |
| `agent_complete`    | 노드 completed + edge completed                            |
| `agent_error`       | 노드 error                                                 |
| `scenario_error`    | 노드 error + 오류 메시지 + 시나리오 상태 `error` (중단)     |
| `agent_status`      | 노드 status 텍스트 / 강조 변경, `workflow`로 실행 중인 Tool 표시 |
| `edge`              | edge 상태 직접 제어 (A2A 응답 등 역방향)                   |
| `message`           | Assistant 메시지 추가                                      |
| `activity`          | 단발성 Activity 카드 추가                                  |
| `session_open`      | A2A / Delegation 세션 시작. 이후 message는 이 세션으로 라우팅 |
| `session_close`     | 가장 안쪽 세션 종료, 결과(result)를 요청 카드에 부착        |
| `scenario_complete` | 사용된 노드 전체 completed, 완료 배지 표시                 |

## 범위 밖

Group Super의 실구현, A2A 프로토콜, 실제 뉴스/공시 API, RAG, Vector DB,
실제 Meeting DB, 실제 valuation 모델, 다중 persona 토론, token streaming, 인증, 배포는 포함하지 않습니다.
