# Executive AI — Multi-Agent Decision Support Demo

대기업 경영진을 위한 Multi-Agent AI 시스템 데모입니다.
고정된 NeuroEdge M&A 8 Step 시나리오를 재생하면서
`Group Super → A2A → K Electronics Super → Agent Delegation → Workflow Tool → 결과 종합`
구조를 채팅과 그래프에서 동시에 보여줍니다.

mock을 한 단계씩 실제 Agent 실행으로 교체하는 중입니다. 현재 상태:

```text
Step 1  MOCK   회장 요청 접수
Step 2  MOCK   A2A 요청 (Group Super → K Super)
Step 3  REAL   K Super → Meeting Agent → run_meeting_context_retrieval   (Python · LangGraph · Ollama Cloud)
Step 4  MOCK   News Agent
Step 5  MOCK   Advisory Agent
Step 6  MOCK   K Super 종합
Step 7  MOCK   A2A 응답
Step 8  MOCK   그룹 최종 판단
```

Sidebar **미팅** 탭의 직접 채팅도 같은 Meeting Agent가 실제로 응답합니다.

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

backend가 꺼져 있거나 LLM 호출이 실패하면 Step 3에서 시나리오가 **오류 · 중단** 상태로 멈추고
K전자 슈퍼에이전트 노드가 error로 표시됩니다 (mock으로 대체하지 않음). 초기화 또는 새 요청으로 재실행합니다.

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
4. 완료 후 Sidebar의 **News** 탭에서 `NeuroEdge 관련 악재만 다시 보여줘.`를 보내면
   같은 News Agent가 사용자 직접 요청에도 응답하며, 그래프의 News 노드가 다시 active 됩니다.
5. **Reset Scenario**로 초기화하고 재실행할 수 있습니다.

## 아키텍처 개념 (UI 표현)

| 개념                   | 의미                                               | UI                                    |
| ---------------------- | -------------------------------------------------- | ------------------------------------- |
| Super Agent            | 요청 이해 · 위임 · 결과 종합 (orchestration)       | indigo 노드                           |
| A2A                    | 독립 Super Agent 시스템 간 통신                    | 굵은 indigo edge, `A2A` 라벨          |
| Agent Delegation       | Super Agent → 자신의 전문 Agent 자연어 Task 위임   | teal 점선 edge, `Delegate` 라벨       |
| Workflow Tool          | 전문 Agent가 Skill에 따라 선택·실행하는 정형 업무  | 노드 안 `run_*` 모노스페이스 라벨     |
| Workflow 내부 단계     | Tool 내부에 캡슐화 (Agent가 단계별 호출하지 않음)  | Advisory 노드 status 텍스트 전환만    |

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
    mockData.ts        기업 / 뉴스 / 회의 고정 데이터, Workflow Tool 이름, run_mna_review 단계
    mockResults.ts     각 Workflow Tool의 구조화된 mock 결과 (시나리오·직접 채팅 공용)
    scenarioEvents.ts  8 Step M&A 시나리오 이벤트 배열 (Step 3은 `live` 이벤트, 나머지는 mock)
    mnaScenario.ts     시나리오 메타 + 트리거 조건 + 예시 문장
    mockResponses.ts   Advisory / News 직접 채팅 응답
  live/
    api.ts             Python backend 클라이언트 (POST /api/k-super/run, /api/agents/meeting/run)
    meetingAgent.ts    Meeting Agent 실행 trace → 세션/메시지 이벤트 · 채팅 블록 변환
  hooks/
    useScenarioRunner.ts  이벤트 재생 → 노드/엣지/채팅 상태. `live` 이벤트를 await 후 결과 이벤트를 큐에 삽입.
                          runDirect()(mock) / runDirectLive()(실제)로 전문 Agent 단독 실행
    useCollapsePhase.ts   open → collapsing → closed 전환 (창/카드 접힘 애니메이션용)
  components/
    Header, Sidebar, ChatPanel, ChatMessage, ActivityCard, ComparisonCard(미사용),
    AgentGraph (React Flow), AgentNode, ScenarioStatus, PanelSwitch,
    SessionCard (세션을 연 요청 카드 + 결과/재생), SessionWindow (세션별 채팅창),
    SessionWindows (열린 세션 창 목록)
  App.tsx

backend/
  app/
    main.py                          FastAPI: /api/health, /api/k-super/run, /api/agents/meeting/run
    llm.py                           ChatOllama (Ollama Cloud, OLLAMA_* 환경변수)
    schemas.py                       응답 스키마 (AgentRun = agent · request · tool_calls · response)
    agents/
      common.py                      MessagesState 기반 공통: tool 실행, trace 추출
      k_super.py                     K전자 슈퍼에이전트 그래프. delegate_to_meeting_agent tool로 위임
      meeting.py                     미팅 에이전트 그래프. run_meeting_context_retrieval tool 사용
    workflows/
      meeting_context_retrieval.py   Workflow Tool: fixture 조회 (키워드 점수 기반)
    data/meetings.json               회의 fixture (NeuroEdge 전략회의 + 무관한 회의 2건)
  tests/                             test_workflow.py (LLM 불필요), test_agents.py (Ollama Cloud 필요)
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
```

- 두 Agent는 각각 독립된 message history를 가집니다. 미팅 에이전트 내부의 tool call / result는
  K Super의 history에 들어가지 않고, 최종 응답만 ToolMessage로 돌아갑니다.
- 전체 trace(위임 문장, tool 호출 인자/결과, 최종 응답)는 API 응답으로 frontend에 전달되어
  기존 세션 창 / 그래프에 표시됩니다.
- Step 4 이후: `k_super.py`의 `TOOLS`에 `delegate_to_news_agent` 등을 추가하고,
  `tools → END` 엣지를 `tools → model`로 바꾸면 K Super가 여러 Agent에 위임하고 종합(Step 6)까지 수행합니다.
  frontend는 `scenarioEvents.ts`의 해당 Step을 `live` 이벤트로 바꾸고 trace → 이벤트 변환을 `src/live/`에 추가합니다.

### 시나리오 로직과 그래프 렌더링 분리

- `scenarioEvents.ts`에는 UI 코드가 없고, `AgentGraph.tsx`에는 시나리오 내용이 없습니다.
- `useScenarioRunner`가 이벤트를 reducer로 `nodes` / `edges` / `messages` 상태로 변환하고,
  그래프와 채팅은 그 상태만 읽습니다.
- `live` 이벤트는 runner가 await한 뒤, 반환된 이벤트 배열을 큐의 현재 위치에 삽입합니다.
  실패하면 `scenario_error`로 시나리오를 중단합니다.

### 이벤트 타입

| type                | 역할                                                       |
| ------------------- | ---------------------------------------------------------- |
| `step`              | 진행 Step 번호 / 제목 갱신                                 |
| `live`              | 실제 Agent 실행. `run()` 결과 이벤트를 제자리에 삽입, 실패 시 중단 |
| `agent_start`       | 노드 active (+ `workflow` 표시) + from이 있으면 edge active |
| `agent_complete`    | 노드 completed + edge completed                            |
| `agent_error`       | 노드 error                                                 |
| `scenario_error`    | 노드 error + 오류 메시지 + 시나리오 상태 `error` (중단)     |
| `agent_status`      | 노드 status 텍스트 / 강조만 변경 (Workflow 내부 단계 표시) |
| `edge`              | edge 상태 직접 제어 (A2A 응답 등 역방향)                   |
| `message`           | Assistant 메시지 추가                                      |
| `activity`          | 단발성 Activity 카드 추가                                  |
| `session_open`      | A2A / Delegation 세션 시작. 이후 message는 이 세션으로 라우팅 |
| `session_close`     | 가장 안쪽 세션 종료, 결과(result)를 요청 카드에 부착        |
| `scenario_complete` | 사용된 노드 전체 completed, 완료 배지 표시                 |

## 범위 밖

News / Advisory / Group Super의 실구현, A2A 프로토콜, 뉴스/공시 API, RAG, Vector DB,
실제 Meeting DB, valuation 계산, token streaming, 인증, 배포는 포함하지 않습니다.
