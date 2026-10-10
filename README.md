# Executive AI — Multi-Agent Decision Support Demo

대기업 경영진을 위한 Multi-Agent AI 시스템의 **deterministic mock demo**입니다.
실제 Agent backend 없이, 고정된 NeuroEdge M&A 시나리오를 재생하면서
`Group Super → A2A → K Electronics Super → Agent Delegation → Workflow Tool → 결과 종합`
구조를 채팅과 그래프에서 동시에 보여줍니다.

## 실행

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # tsc + vite build
```

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
    scenario.ts        ScenarioEvent · ChatItem · MessageBlock · RunnerState
  scenario/
    mockData.ts        기업 / 뉴스 / 회의 고정 데이터, Workflow Tool 이름, run_mna_review 단계
    mockResults.ts     각 Workflow Tool의 구조화된 mock 결과 (시나리오·직접 채팅 공용)
    scenarioEvents.ts  8 Step M&A 시나리오 이벤트 배열 (delay 포함)
    mnaScenario.ts     시나리오 메타 + 트리거 조건 + 예시 문장
    mockResponses.ts   Advisory / News / Meeting 직접 채팅 응답
  hooks/
    useScenarioRunner.ts  이벤트 재생 → 노드/엣지/채팅 상태. runDirect()로 전문 Agent 단독 실행
  hooks/
    useCollapsePhase.ts   open → collapsing → closed 전환 (창/카드 접힘 애니메이션용)
  components/
    Header, Sidebar, ChatPanel, ChatMessage, ActivityCard, ComparisonCard(미사용),
    AgentGraph (React Flow), AgentNode, ScenarioStatus, PanelSwitch,
    SessionCard (세션을 연 요청 카드 + 결과/재생), SessionWindow (세션별 채팅창),
    SessionWindows (열린 세션 창 목록)
  App.tsx
```

### 시나리오 로직과 그래프 렌더링 분리

- `scenarioEvents.ts`에는 UI 코드가 없고, `AgentGraph.tsx`에는 시나리오 내용이 없습니다.
- `useScenarioRunner`가 이벤트를 reducer로 `nodes` / `edges` / `messages` 상태로 변환하고,
  그래프와 채팅은 그 상태만 읽습니다.
- 추후 실제 Agent backend가 생기면 타이머 재생 대신 live 이벤트를 같은 reducer에
  dispatch하면 됩니다.

### 이벤트 타입

| type                | 역할                                                       |
| ------------------- | ---------------------------------------------------------- |
| `step`              | 진행 Step 번호 / 제목 갱신                                 |
| `agent_start`       | 노드 active (+ `workflow` 표시) + from이 있으면 edge active |
| `agent_complete`    | 노드 completed + edge completed                            |
| `agent_status`      | 노드 status 텍스트 / 강조만 변경 (Workflow 내부 단계 표시) |
| `edge`              | edge 상태 직접 제어 (A2A 응답 등 역방향)                   |
| `message`           | Assistant 메시지 추가                                      |
| `activity`          | 단발성 Activity 카드 추가                                  |
| `session_open`      | A2A / Delegation 세션 시작. 이후 message는 이 세션으로 라우팅 |
| `session_close`     | 가장 안쪽 세션 종료, 결과(result)를 요청 카드에 부착        |
| `scenario_complete` | 사용된 노드 전체 completed, 완료 배지 표시                 |

## 범위 밖

실제 LLM 호출, A2A 프로토콜, 뉴스/공시 API, RAG, Vector DB, Meeting DB,
valuation 계산, 인증, DB, 배포, LangGraph 등은 포함하지 않습니다.
