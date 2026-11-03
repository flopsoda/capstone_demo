# Executive AI — Multi-Agent Decision Support Demo

대기업 경영진을 위한 Multi-Agent AI 시스템 데모입니다.
같은 Agent Platform runtime 위에 **독립된 Agent System 두 개**(K그룹 · K전자)가 있고, 둘은 A2A(HTTP)로만 통신합니다.

```text
User → Group Super → (A2A) → K Electronics Super → delegation → Meeting / News / Advisory → Workflow Tool
                                                 ← K전자 최종 응답
     ← 그룹 최종 판단
```

NeuroEdge M&A 데모의 Step 1~8이 전부 실제 Agent 실행입니다 (Step은 Demo Monitor의 표시 개념일 뿐 backend는 모릅니다):

```text
Step 1  REAL   회장 요청 접수                         Group UI → Group backend (:8001)
Step 2  REAL   A2A 요청 (Group Super → K Super)       Group Super의 Tool이 K backend(:8000) /api/a2a/run 호출, 요청문은 LLM이 작성
Step 3  REAL   K Super → Meeting Agent → run_meeting_context_retrieval
Step 4  REAL   K Super → News Agent → run_company_risk_news_analysis     (같은 K Super 실행 안에서 순차 위임)
Step 5  REAL   K Super → Advisory Agent → run_mna_review                 (Meeting · News 결과를 맥락으로 담아 위임)
Step 6  REAL   K Super 종합                                              (Tool 호출 없이 최종 검토 결과 → agent_end)
Step 7  REAL   A2A 응답                                                  K의 최종 응답 텍스트만 Group Super에게 전달
Step 8  REAL   그룹 최종 판단                                            Group Super의 마지막 model 턴
```

두 플랫폼 UI(`/group`, `/k`) 모두 **슈퍼에이전트 · 경영자문 · 뉴스 · 미팅** 탭의 직접 채팅을 제공하며, 협업형 요청에서는 A2A / 위임 세션이 채팅 옆 창으로 펼쳐집니다.
직접 채팅에서는 Tool 사용이 강제되지 않아, 일반 개념 질문에는 Tool 없이 답하고 실제 검토 요청에는 Workflow Tool · 위임 · A2A를 사용합니다.

## 실행

요구 버전: **Node `^20.19 || >=22.12`** (Vite 8 · oxlint), **Python `>=3.12`** (3.13으로 검증). 외부 의존은 LLM API key(기본 Ollama Cloud, `LLM_PROVIDER=openai`면 OpenAI 호환 API)와
미팅 에이전트의 회의 저장소 **Postgres 15+**(`MEETINGS_DATABASE_URL`, `pg_trgm` 사용)입니다 — `backend/.env.example`.
각 system은 처음 쓸 때 자기 schema(K `kelec`, Group `grp`)를 만들고(이후 `meeting_migrations/`의 번호 migration 적용) `data/meetings.json`으로 채웁니다. 저장소가 없으면 미팅 Tool은 오류 결과를 돌려줍니다.
선택: 녹음 transcript 연결(`SPEECH_SERVICE_URL`), 메일 받는 사람 조회(`DIRECTORY_URL`), 메일 발송(`SMTP_*`, `MEETINGS_BACKGROUND_JOBS=1`), 서명된 요청자(`PRINCIPAL_SIGNING_KEY`) — 배포(gateway 레포)가 채웁니다.
로컬 데모에서 회의록을 확정하려면 `MEETINGS_CONFIRM_GROUPS=*` (principal 없는 요청은 그룹이 없으므로).

```bash
# 1) Python backend — K Electronics system (별도 터미널)
cd backend
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # OLLAMA_API_KEY 입력 (https://ollama.com/settings/keys), MEETINGS_DATABASE_URL 입력
.venv/bin/uvicorn app.main:app --port 8000 --reload

# 1b) Python backend — Group system (별도 터미널, 같은 venv/.env). Group Super가 K에 A2A로 요청한다
.venv/bin/uvicorn app.systems.group.app:app --port 8001 --reload

# 2) React frontend
npm install
npm run dev      # http://localhost:5173  (/api → :8000 K, /group-api → :8001 Group proxy)
npm run build    # tsc + vite build

# 3) 브라우저 창
#    http://localhost:5173/group    Group Platform UI (제품)  ← 기본 경로 / 도 여기
#    http://localhost:5173/k        K Electronics Platform UI (제품)
#    http://localhost:5173/monitor  Demo Monitor (테스트 · 발표용 관찰 창, 제품 UI 아님):
#                                   Group backend의 /api/events(SSE)를 구독해 전체 Agent Graph · Step 1~8 · 협업 세션을 실제 이벤트로만 보여준다
```

backend 테스트:

```bash
cd backend
.venv/bin/python -m pytest        # tests/test_agents.py는 OLLAMA_API_KEY가 없으면 skip
                                  # 키가 있으면 실제 Ollama Cloud를 호출한다 (수 분 소요 · 과금). 빠른 로컬 검증은:
.venv/bin/python -m pytest --ignore=tests/test_agents.py
# 회의 저장소 테스트(test_meeting_records 등)는 MEETINGS_TEST_DATABASE_URL이 있을 때만 실행된다 (없으면 skip).
# 이름에 test가 들어간 버릴 DB를 쓴다: 테스트마다 그 DB의 kelec / grp schema를 지우고 다시 만든다.
MEETINGS_TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5432/agents_test .venv/bin/python -m pytest
```

세션창 · 메시지 · (Monitor의) 그래프와 Step은 backend가 실행 중 보내는 lifecycle 이벤트(요청자에게는 NDJSON 스트림, 관찰자에게는 SSE)를 받는 즉시 바뀝니다.
각 Agent의 응답은 토큰 단위로 스트리밍되어 글자가 자라는 초안으로 보이다가 최종 응답으로 확정됩니다.
Group Super가 자기 전문 Agent에게 위임할지 K전자에 A2A로 요청할지, K Super가 어떤 전문 Agent에게 위임할지, 언제 멈추고 최종 응답을 쓸지는
요청과 누적된 결과를 보고 매 턴 스스로 판단합니다 (등록된 Tool을 모두 호출해야 하는 규칙은 없습니다). 모든 메시지는 backend 응답 문자열 그대로입니다.
backend가 꺼져 있거나 Agent가 실패하면(LLM 오류, 전문 Agent가 Workflow Tool을 호출하지 않음, A2A 상대에 연결 불가 등) 그 시점에
**오류 · 중단**으로 멈추고, backend가 지목한 Agent가 error로 표시됩니다 (mock으로 대체하지 않음). 새 요청으로 재실행합니다.

## 시연 순서

창 세 개를 띄웁니다: `/group`, `/k`, `/monitor`.

1. **Group UI**의 **그룹 슈퍼에이전트** 탭에서 요청을 보냅니다 (빈 채팅의 **예시 요청 불러오기**로 대표 문장 입력).
2. Group Super가 요청을 판단해 K전자에 A2A로 검토를 요청합니다. 채팅 옆에 **A2A 대화** 창이 열리고, 그 안에 K전자 슈퍼에이전트가 미팅 → 뉴스 → 경영자문에게
   위임하는 **위임 대화** 창이 차례로 열렸다 닫힙니다 (K 내부 lifecycle이 관찰용으로 relay됨). K의 최종 응답이 돌아오면 A2A 창이 요청 카드로 접히고,
   Group Super가 그룹 최종 판단을 씁니다. 필요하다고 판단하면 그룹 자체 전문 Agent에게도 위임합니다.
3. **Demo Monitor**는 같은 실행을 전체 그래프(두 시스템 9노드)와 Step 1~8로 보여줍니다. 제품 UI에는 그래프와 Step이 없습니다.
4. **K UI**는 그룹의 A2A 요청이 처리되는 동안 Sidebar에 "외부 요청 처리 중 · 그룹" 배지가 켜지고, **K전자 슈퍼에이전트** 탭에 "외부 A2A 요청 · 그룹" 카드와 K 내부 위임 창이 나타났다가 "응답 반환"으로 접힙니다 (K UI를 K Super 탭에 둔 채 Group UI 옆에 띄우면 양쪽 관점이 동시에 보입니다). 그 밖에 K전자 측 직접 채팅: **뉴스** 탭에 `뉴로엣지 관련 악재만 다시 보여줘.`, **경영자문** 탭에 `뉴로엣지 인수를 CFO 관점에서 검토해줘.`
   (`earn-out이 뭐야?` 같은 개념 질문에는 Tool 없이 답함), **K전자 슈퍼에이전트** 탭에 `지난 회의에서 정한 기준만 정리해줘.`(미팅 위임 창이 열림).
   Group UI의 **미팅** 탭은 그룹 투자심의 회의 기록(Group 데이터)으로 답합니다. 미팅 탭에서는 회의 transcript로 회의록 초안을 저장 · 확정하고
   (`save_meeting_minutes`: transcript id 연결 · Compliance 검토 · 확정 권한), Action Item 진행을 조회 · 갱신하며(`run_action_item_tracking` · `update_action_item`), 회의 일정을 예약하고(`run_meeting_schedule`),
   회의록 · 업무 배정 · 회의 안내 · 기한 알림 메일은 Agent가 받는 사람의 주소를 찾아 바로 보내고, 보낸 메일을 조회합니다(`manage_mail_outbox`).
   회의록 메일에는 회의록 PDF가, 의장 · 간사에게는 회의 결과 보고서 PDF도 첨부됩니다(`meeting_report`, 글꼴: `fonts-nanum` 또는 `REPORT_FONT_DIR`). K 미팅 탭은 그룹이 A2A로 보낸 업무 요청을 접수 · 등록합니다(`run_task_requests`).
5. Monitor의 **초기화**로 관찰 화면을 비우고 다시 시연할 수 있습니다. 채팅 전사는 탭마다 유지됩니다.

## 아키텍처 개념 (UI 표현)

| 개념                   | 의미                                               | UI                                    |
| ---------------------- | -------------------------------------------------- | ------------------------------------- |
| Super Agent            | 요청 이해 · 위임 · 결과 종합 (orchestration)       | indigo 노드                           |
| A2A                    | 독립 Super Agent 시스템 간 통신                    | 굵은 indigo edge, `A2A` 라벨          |
| Agent Delegation       | Super Agent → 자신의 전문 Agent 자연어 Task 위임   | teal 점선 edge, `Delegate` 라벨       |
| Workflow Tool          | 전문 Agent가 Skill에 따라 선택·실행하는 정형 업무  | 노드 안 `run_*` 모노스페이스 라벨     |
| Workflow 내부 단계     | Tool 내부에 캡슐화 (Agent가 단계별 호출하지 않음)  | 표시하지 않음 (`tool_end` 결과만)     |

그래프(Demo Monitor)의 노드 9개: Group 회장, Group Super, 그룹 미팅 · 뉴스 · 경영자문, K Electronics Super, K 미팅 · 뉴스 · 경영자문.
제품 UI에는 그래프가 없고 Sidebar 탭(슈퍼 + 전문가 3)과 협업 세션창만 있습니다.

## 구조

```text
src/
  types/
    agent.ts           AgentId(group_* 포함) · 상태 · CommunicationType(a2a | delegation) · 토폴로지 · GraphUniverse(DEFAULT 6노드 / FULL 9노드)
    scenario.ts        ScenarioEvent(UI 이벤트 어휘) · ChatItem · MessageBlock · Session · RunnerState
  live/
    api.ts             backend NDJSON 스트림 클라이언트(POST .../run) · SSE 관찰 구독(subscribeAgentEvents, GET /api/events) · K/Group 경로 상수 · 이벤트 stamp 타입
    agentEvents.ts     backend lifecycle 이벤트 → ScenarioEvent 변환 (delegation·a2a 세션, Workflow 결과 블록, Super 최종 응답 메시지; resolveAgent로 system별 id 해석)
  monitor/
    DemoMonitor.tsx    /monitor — 테스트·발표용 관찰 화면 (Group 관찰 채널 구독, 9노드 전체 그래프, Step 1~8, 협업 세션)
    steps.ts           실제 lifecycle 이벤트 → Step 1~8 해석 (backend는 Step을 모름)
  state/
    runState.ts        reducer: 이벤트 → nodes / edges / messages / sessions 상태; createEventQueue: 도착 순서대로 즉시 적용 (delay는 다음 이벤트만 보류)
  hooks/
    useDemoObserver.ts    SSE 관찰 채널 → 같은 mapper·reducer (run 시작 감지, done/error 처리). 아무것도 시작하지 않음
    useCollapsePhase.ts   open → collapsing → closed 전환 (창/카드 접힘 애니메이션용)
  components/
    Header, Sidebar, ChatPanel, ChatMessage, ActivityCard,
    AgentGraph (React Flow), AgentNode, ScenarioStatus, PanelSwitch,
    SessionCard (세션을 연 요청 카드 + 결과/재생), SessionWindow (세션별 채팅창),
    SessionWindows (열린 세션 창 목록)
  platform/
    config.ts          플랫폼별 설정: GROUP_PLATFORM / K_PLATFORM (탭 4개의 id · 라벨 · backend 경로 · 헤더 · 예시 요청, resolveAgent)
    usePlatformChats.ts  탭별 전사·세션 상태. send()가 해당 backend에 NDJSON 요청 → 같은 mapper·reducer로 적용
    PlatformApp.tsx    제품 UI: Header · Sidebar · ChatPanel · SessionWindows (그래프 · Step 없음)
    useInboundRuns.ts  (K) 자기 backend 관찰 채널에서 origin=a2a run을 K Super 탭에 수신 카드·위임 창으로 표시, Sidebar 배지
  main.tsx             URL 경로로 화면 선택 (/group 기본 · /k · /monitor)

backend/
  app/
    main.py                          `uvicorn app.main:app` 진입점. K Electronics system의 app을 재노출
    runtime/                         공통 runtime (Agent id · Capability 내용 · 데이터를 모름)
      agent_core.py                  Specialist core(Specialist / build_specialist_graph / run_specialist, 호출 맥락 delegation|direct)
                                     + Super core(SuperAgent / build_super_graph / run_super, 호출 맥락 a2a|direct), system prompt 조립, 응답 추출
      capabilities.py                Skill / Capability 타입 + Registry 조회·검증 메커니즘
      events.py                      emit() / RunContext(run_id · system · origin · trace_id · caller stamp, principal은 stamp 안 함) / stream_run() / EventHub(HUB: 프로세스 전역 관찰 fan-out), AgentError
      llm.py                         ChatOllama (Ollama Cloud, OLLAMA_*) 또는 LLM_PROVIDER=openai면 ChatOpenAI (OpenAI 호환 API, OPENAI_*)
      schemas.py                     요청 스키마 · Principal(요청한 사용자: email · name · groups · clearance · 쓸 수 있는 전문 Agent — gateway가 보내고 Tool이 run에서 읽음) · principal_token
      principal.py                   gateway가 서명한 principal_token(HS256) 검증 — PRINCIPAL_SIGNING_KEY가 있으면 서명된 사용자만 믿음
      api.py                         NDJSON 스트리밍 helper · run_identity(요청 → principal, 거부면 401) · SSE 관찰 채널 helper · lifespan(on_start: system의 배경 작업)
      a2a.py                         A2A client 메커니즘: 상대 system의 A2A 표면에 요청(+ principal · 토큰)을 보내고 최종 응답을 받음 (lifecycle은 observe 콜백으로)
    library/                         공용 Capability 라이브러리 (system을 import하지 않음)
      skills/                        specialist_delegation, meeting_context_analysis, meeting_minutes_writing, meeting_follow_up, inbound_task_requests, external_risk_analysis, mna_feasibility_review (Skill 본문)
      tools/                         run_company_risk_news_analysis, run_mna_review — make_<tool>(path) factory;
                                     run_meeting_context_retrieval, save_meeting_minutes, run_action_item_tracking, update_action_item, run_meeting_schedule,
                                     manage_mail_outbox, run_task_requests — make_<tool>(store) factory;
                                     correct_transcript — make_transcript_correction(store, glossary, agent): 회의 Agent 앞의 녹음 변환본 교정 + 다시 교정하는 Tool
                                     (Tool 이름 · 설명 · schema의 source of truth); meeting_access.py — 요청자(viewer)를 run에서 읽음; delegation.py — make_delegate_tool(name, description, from, to, run)
      workflows/
        meeting_store.py             회의 저장소: MeetingStore(schema, seed) — Postgres 연결 · schema 생성(meeting_schema.sql) · migration · fixture seed · 등급 필터(VISIBLE)
                                     · 확정 권한(can_confirm) · 사람/조직 디렉터리
        meeting_schema.sql           회의 · 참석 · 안건 · 회의록 · 결정 · Action Item · 진행 이력 · transcript 테이블 (15개, version 1)
        meeting_migrations/          002_delivery_and_review.sql: transcript id · compliance_flags · outbox · task_requests; 003: 주소 없는 메일 반려 허용
        meeting_context_retrieval.py Tool 내부 구현: 열람 가능한 확정 회의록을 키워드 점수로 조회 (LangChain 의존 없음)
        meeting_minutes.py           회의록 초안 저장 · 확정(담당 · 기한 · Compliance · 권한 검사) · 확정본 정정(사유 + 이전 버전 snapshot) · transcript 연결 · 메일 생성
        compliance.py                위험 발언 규칙(자본시장법 · 공정거래법 · 청탁금지법 · 외부감사법 · 노동조합법 · 개인정보) → compliance_flags, 검토(cleared | acknowledged)
        transcripts.py               speech-service에서 transcript id로 가져와 복사 · [mm:ss] 근거 발언 연결 · 만료 삭제
        meeting_report.py            회의록(양식) · 회의 결과 보고서(개조식) PDF — 회의 업무 화면 내려받기 · 회의록 메일 첨부
        outbox.py                    메일 발송함: 회의록 · 업무 배정 · 회의 안내 · 기한 알림 생성, 주소 자동 탐색(로그인 이메일 → 임직원 연락처), 자동 SMTP 발송, 재발송 · 취소 · 주소 정정
        directory.py                 메일 받는 사람의 등록 이메일 조회 (gateway 내부 API)
        jobs.py                      배경 작업: 메일 발송(매분) · 기한 알림 · 주소 재조회 · 만료 transcript 삭제(매일)
        task_requests.py             다른 system의 A2A 업무 요청: submit(A2A) → accept | reject(확정 권한자) → Action Item
        action_items.py              Action Item 진행 조회(지연 순, 상태별 건수) · 갱신(이력 기록)
        meeting_schedule.py          회의 일정 조회 · 예약(이전 회의의 보류 안건 · 미결 쟁점 이월) · 변경 · 취소 (참석자 안내 메일 생성)
        company_risk_news_analysis.py 기업 alias + 리스크 category 키워드로 기사 store(path) 조회
        mna_review.py                기업 store(path) + persona(CEO/CFO/CTO)별 평가 기준 + context 신호 → 항목별 평가 · 권고 · 조건
    systems/
      group/                         Group agent system (SYSTEM = "group") — `uvicorn app.systems.group.app:app --port 8001`
        app.py                       FastAPI: /api/health, /api/agents/{group-super,meeting,news,advisory}/run (직접 채팅; NDJSON 스트림), /api/events (SSE)
        registry.py                  Group의 CAPABILITIES (20개): group_super ← subsidiary_review · specialist_delegation · request_k_electronics_review · delegate_to_*;
                                     meeting / news / advisory ← 라이브러리 Skill + Group 데이터 · 회의 저장소(schema grp)에 바인딩한 Tool
        agents/group_super.py        그룹 슈퍼에이전트: AGENT_ID · IDENTITY + Registry (Super core). 자기 Specialist 위임과 K전자 A2A 중 선택은 모델 판단
        agents/{meeting,news,advisory}.py  그룹 전문 Agent: 같은 Specialist core · 같은 Skill, Group 데이터
        skills/subsidiary_review.py  계열사 검토 요청(A2A)과 그룹 판단 지침
        tools/delegate_to_*.py       Group Super → 그룹 전문 Agent 위임 Tool (라이브러리 factory + Group 전용 설명)
        tools/run_*.py 등            라이브러리 Tool factory를 Group data/ · MEETING_STORE에 바인딩한 instance
        tools/request_k_electronics_review.py  A2A client Tool: a2a_start → K /api/a2a/run (HTTP) → K lifecycle relay(관찰용) → 최종 응답만 반환 → a2a_end
                                     (검토 요청 · 업무 전달 · 전달한 업무의 진행 조회)
        data/                        그룹 fixture: 그룹 투자심의·경영전략 회의(회의 저장소 seed), 계열사·그룹 투자 대상 뉴스, 그룹 차원 인수 후보(브라이트셀)
      k_electronics/                 K Electronics agent system (SYSTEM = "k_electronics", DATA_DIR)
        app.py                       FastAPI: /api/health, /api/a2a/run (A2A 수신), /api/agents/{k-super,meeting,news,advisory}/run (직접 채팅; NDJSON 스트림), /api/events (SSE 관찰 채널)
        registry.py                  이 system의 CAPABILITIES와 allowed_agents (권한의 source of truth), skills_for / tools_for
        agents/
          k_super.py                 K전자 슈퍼에이전트: AGENT_ID · IDENTITY + Registry 위임 Tool (Super core)
          meeting.py                 미팅 에이전트: AGENT_ID · IDENTITY + Registry Skill 4개 / Tool 7개(회의 조회 · 회의록 저장 · 진행 조회 · 진행 갱신 · 일정 · 메일 발송함 · 업무 요청)
          news.py                    뉴스 에이전트: AGENT_ID · IDENTITY + Registry Skill/Tool(run_company_risk_news_analysis)
          advisory.py                경영자문 에이전트: AGENT_ID · IDENTITY + Registry Skill/Tool(run_mna_review)
        tools/
          delegate_to_*.py           K Super → 미팅 / 뉴스 / 경영자문 위임 Tool (라이브러리 factory + K 설명; delegation_start/end 발행, 최종 응답만 반환, 사용자가 쓸 수 없는 Agent면 delegation_skipped)
          run_*.py 등                라이브러리 Tool factory를 이 system의 data/ · MEETING_STORE(schema kelec)에 바인딩한 instance
        data/meetings.json           회의 저장소 seed (NeuroEdge 전략회의(대외비) + 무관한 회의 2건, Action Item 기한 포함)
        data/news.json               뉴스 fixture (NeuroEdge 리스크 기사 3건 + 무관한 기업 기사 2건)
        data/companies.json          인수 대상 기업 fixture (NeuroEdge 재무 · 가치 · 고객 · 기술 · 전략)
  tests/                             test_workflow.py · test_meeting_records.py · test_meeting_delivery.py (회의 저장소 필요: conftest.py의 meeting_db) · test_news_workflow.py · test_mna_review_workflow.py
                                     · test_capabilities.py · test_k_super.py · test_k_app.py · test_events.py · test_group.py · test_group_workflows.py
                                     · test_boundaries.py(runtime/library/system import 경계) (LLM 불필요), test_agents.py (Ollama Cloud 필요)
```

### Agent 구조 (backend)

```text
K전자 슈퍼에이전트 (LangGraph, MessagesState)
  model ──tool call──▶ delegate_to_meeting_agent(task)   ← LLM이 자연어 업무를 작성해 호출
                              │ invoke
                              ▼
                       미팅 에이전트 (LangGraph, MessagesState)
                         model ──tool call──▶ run_meeting_context_retrieval(query, topics, ...)
                               ◀── tool result ──┘        (회의 저장소 schema kelec, 요청자 등급으로 필터)
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
| `agent_token`                       | 응답이 글자 단위로 자라는 초안 메시지 (caret). 최종 `agent_end` 메시지가 대체       |
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
- Group Super(:8001)는 요청을 보고 자기 전문 Agent 위임과 K전자 A2A 요청(`request_k_electronics_review` → K `/api/a2a/run`) 중에서 스스로 고릅니다. K의 lifecycle은 관찰용으로 Group 스트림에 relay되고, Group Super Agent 자체는 K의 최종 응답 텍스트만 받습니다.

### 이벤트 처리와 렌더링 분리

- `src/live/agentEvents.ts`가 backend 이벤트를 UI 이벤트(아래 표)로 바꾸고, `src/state/runState.ts`의 reducer가 그것을 `nodes` / `edges` / `messages` / `sessions` 상태로 만듭니다.
  채팅 · 세션창 · (Monitor의) 그래프는 그 상태만 읽습니다. 시나리오 내용이 UI 코드에 없고, UI 코드에 시나리오가 없습니다.
- 이벤트는 도착 순서대로 바로 적용됩니다. 이벤트의 `delay`는 상태 변경을 늦추지 않고 다음 이벤트만 잠시 보류합니다(결과 읽을 시간 후 세션 닫힘).
  실패하면 backend가 지목한 Agent로 `scenario_error`를 적용하고 중단합니다.

### UI 이벤트 타입

| type                | 역할                                                       |
| ------------------- | ---------------------------------------------------------- |
| `step`              | 진행 Step 번호 / 제목 갱신 (Demo Monitor만)                |
| `agent_start`       | 노드 active (+ `workflow` 표시) + from이 있으면 edge active |
| `agent_complete`    | 노드 completed + edge completed                            |
| `agent_error`       | 노드 error                                                 |
| `scenario_error`    | 노드 error + 오류 메시지 + 열린 세션 닫힘 + 상태 `error` (중단) |
| `agent_status`      | 노드 status 텍스트 / 강조 변경, `workflow`로 실행 중인 Tool 표시 |
| `edge`              | edge 상태 직접 제어. reducer만 처리하며 현재 생성하는 곳은 없음 (mock scenario 시절 어휘) |
| `message`           | Assistant 메시지 추가 (`final`이면 같은 발신자의 스트리밍 초안을 대체) |
| `message_delta`     | 토큰 조각: 발신자의 초안 메시지를 제자리에서 키움 (없으면 생성)  |
| `message_commit`    | 발신자가 Tool/위임/A2A로 넘어감: 초안을 그대로 확정           |
| `activity`          | 단발성 Activity 카드 추가. reducer · 렌더만 있고 현재 생성하는 곳은 없음 (mock scenario 시절 어휘) |
| `session_open`      | A2A / Delegation 세션 시작. 이후 message는 이 세션으로 라우팅 |
| `session_close`     | 가장 안쪽 세션 종료, 결과(result)를 요청 카드에 부착        |
| `scenario_complete` | 사용된 노드 전체 completed, 완료 배지 표시 (Demo Monitor)  |

## 범위 밖

표준 A2A 프로토콜(이 레포의 A2A는 두 system 사이의 자체 약속: NDJSON lifecycle + 최종 응답), 실제 뉴스/공시 API, RAG, Vector DB,
실제 valuation 모델, 다중 persona 토론, 인증, 배포는 포함하지 않습니다. 사용자 인증은 이 레포 앞단(gateway)의 몫이며,
backend는 `PRINCIPAL_SIGNING_KEY`가 있으면 gateway가 서명한 principal만 믿습니다. 메일 회신으로 진행을 갱신하는 기능은 없습니다 (docs/architecture.md 12장 18번).
