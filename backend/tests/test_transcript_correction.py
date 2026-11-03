"""녹음 변환본 교정: 고정 치환 → 모델 교정 (실패하면 치환만 남기고 알림) → Meeting Agent (실패 시 correct_transcript로 한 번 더)."""

import json
import time

import pytest

from app.library.tools import correct_transcript as ct
from app.library.workflows import transcript_correction as tc
from app.runtime.events import new_run, stream_run
from app.systems.group import DATA_DIR

GLOSSARY = tc.Glossary(terms=("에이전트", "온톨로지"), replacements=(("에젠드", "에이전트"),))
TID = "TR-20261103-1A2B3C"


def lines(n):
    return [f"[{i // 60:02d}:{i % 60:02d}] 화자 {'AB'[i % 2]}: 발언 {i + 1}번 입니다" for i in range(n)]


class Model:
    """가짜 모델: 받은 프롬프트를 기록하고, 차례대로 답한다 (예외면 그 예외를 낸다)."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, system, user):
        self.prompts.append(user)
        answer = self.answers.pop(0) if self.answers else "[]"
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def test_fixed_replacements_apply_even_without_the_model():
    out = tc.correct_lines(["[00:01] 화자 A: 에젠드 만들기"], GLOSSARY, [], None)
    assert out.lines == ["[00:01] 화자 A: 에이전트 만들기"]
    assert (out.changed, out.by_replacement, out.by_model, out.changes) == (1, 1, 0, [(1, "에젠드", "에이전트")])


def test_the_model_fixes_words_but_a_rewrite_is_refused():
    src = ["[00:01] 화자 A: 온토 로지 를 써서 그래프 로 저장", "[00:05] 화자 B: 그건 좀 어렵 겠네요", "[00:09] 화자 A: 네"]
    model = Model([{"n": 1, "text": "온톨로지를 써서 그래프로 저장"},
                   {"n": 2, "text": "이 방식은 비용과 일정 면에서 현실적이지 않다고 봅니다"},  # 다시 쓴 문장 → 버림
                   {"n": 9, "text": "범위 밖"}])
    out = tc.correct_lines(src, GLOSSARY, ["이서연"], model)
    assert out.lines == ["[00:01] 화자 A: 온톨로지를 써서 그래프로 저장", src[1], src[2]]  # 화자 · 시각은 그대로
    assert (out.by_model, out.rejected) == (1, 1)
    assert ("온토 로지 를", "온톨로지를") in [(b, a) for _, b, a in out.changes]
    prompt = model.prompts[0]
    assert "용어: 에이전트, 온톨로지" in prompt and "이름: 이서연" in prompt and "[1] 화자 A: 온토 로지" in prompt
    assert "1건은 버림" in out.summary()
    # 모델이 줄 번호 · 화자 라벨까지 베껴 써도 발언만 남긴다 (gemma4에서 본 일)
    echoed = tc.correct_lines(src[:1], GLOSSARY, [], Model([{"n": 1, "text": "[1] 화자 A: 온톨로지를 써서 그래프로 저장"}]))
    assert echoed.lines == ["[00:01] 화자 A: 온톨로지를 써서 그래프로 저장"]


def test_long_transcripts_go_in_chunks_with_context_and_a_failed_chunk_is_reported():
    progress = []
    model = Model([{"n": 2, "text": "발언 2번입니다"}], RuntimeError("boom"), [{"n": 61, "text": "발언 61번입니다"}])
    out = tc.correct_lines(lines(65), GLOSSARY, [], model, progress=lambda done, total: progress.append((done, total)))
    assert len(model.prompts) == 3 and progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert "앞 맥락 (고치지 말 것):\n[28]" in model.prompts[1] and "고칠 줄 (31–60)" in model.prompts[1]
    assert "뒤 맥락 (고치지 말 것):\n[31]" in model.prompts[0]
    assert out.lines[1].endswith("발언 2번입니다") and out.lines[60].endswith("발언 61번입니다")
    assert out.failed == ["줄 31–60: RuntimeError"] and "모델 교정 실패: 줄 31–60" in out.summary()


def test_an_unreadable_or_late_answer_fails_only_that_chunk():
    out = tc.correct_lines(lines(2), GLOSSARY, [], Model("잘 모르겠습니다"))
    assert out.failed == ["줄 1–2: 답에 JSON 배열이 없습니다"] and out.lines == lines(2)

    def slow(system, user):
        time.sleep(1)
        return "[]"

    out = tc.correct_lines(lines(2), GLOSSARY, [], slow, timeout=0.05)
    assert out.failed and "초 안에 답이 없습니다" in out.failed[0]
    assert tc.parse_answer('```json\n[{"n": 1, "text": "가"}, {"n": "x"}]\n```', 1, 1) == {1: "가"}


def test_only_transcript_lines_of_a_text_change():
    text = f"녹음 파일: 회의.m4a\n[transcript_id: {TID}]\n(화자 자동 식별 transcript)\n[00:01] 화자 A: 에젠드\n끝"
    fixed, out = tc.correct_text(text, GLOSSARY, [], None)
    assert fixed == text.replace("화자 A: 에젠드", "화자 A: 에이전트") and out.changed == 1
    assert tc.correct_text("회의록 보여 줘", GLOSSARY, [], None) is None


class NoStore:
    def connect(self):
        raise RuntimeError("no database")


@pytest.fixture
def fixer(tmp_path):
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps({"terms": ["에이전트"], "replacements": {"에젠드": "에이전트"}}, ensure_ascii=False), encoding="utf-8")
    return ct.TranscriptCorrection(NoStore(), path, agent="meeting")


REQUEST = ("[이전 대화]\n사용자: [00:01] 화자 A: 에젠드 (지난 녹음)\n\n[현재 요청]\n녹음 파일: 회의.m4a · 출석 확인: CHK-ABC234\n"
           f"[transcript_id: {TID}]\n[00:01] 화자 A: 에젠드 를 만들 려고")


def run_prepare(fixer, request):
    out = []
    events = list(stream_run(lambda: out.append(fixer.prepare(request)), run=new_run(system="group", root_agent="meeting")))
    return out[0], [e for e in events if e["type"].startswith("tool_")]


def test_prepare_corrects_the_current_request_and_says_what_changed(fixer, monkeypatch):
    monkeypatch.setattr(ct, "ask_model", Model([{"n": 1, "text": "에이전트를 만들려고"}]))
    out, events = run_prepare(fixer, REQUEST)
    assert "사용자: [00:01] 화자 A: 에젠드 (지난 녹음)" in out  # 이전 대화는 그대로
    assert "[00:01] 화자 A: 에이전트를 만들려고\n\n[자동 교정] " in out and "변환본 1줄 중 1줄 교정" in out
    assert [e["type"] for e in events] == ["tool_start", "tool_end"]
    assert events[0]["args"] == {"transcript_id": TID, "lines": 1, "auto": True} and events[0]["tool"] == "correct_transcript"
    assert events[1]["result"]["auto"] and events[1]["result"]["failed"] == []


def test_prepare_reports_a_failed_model_and_points_the_agent_at_the_tool(fixer, monkeypatch):
    monkeypatch.setattr(ct, "ask_model", Model(ConnectionError("ollama down")))
    out, events = run_prepare(fixer, REQUEST)
    assert "[00:01] 화자 A: 에이전트 를 만들 려고" in out  # 고정 치환은 적용됨
    assert "[자동 교정 실패]" in out and f'correct_transcript(transcript_id="{TID}")' in out
    assert events[1]["result"]["failed"] == ["줄 1–1: ConnectionError"]


def test_prepare_leaves_other_requests_alone(fixer, monkeypatch):
    out, events = run_prepare(fixer, "지난주 회의록 보여 줘")
    assert out == "지난주 회의록 보여 줘" and events == []
    monkeypatch.setenv("TRANSCRIPT_CORRECTION", "0")
    assert run_prepare(fixer, REQUEST) == (REQUEST, [])


def test_the_tool_corrects_the_kept_transcript(fixer, monkeypatch):
    monkeypatch.setattr(ct, "fetch_transcript", lambda ref: {"id": ref, "turns": [
        {"start": 1.0, "end": 3.0, "speaker": "화자 A", "text": "에젠드 를 만들 려고"}]})
    monkeypatch.setattr(ct, "ask_model", Model([{"n": 1, "text": "에이전트를 만들려고"}]))
    tool = ct.make_transcript_correction(NoStore(), fixer.glossary_path, agent="meeting").tool
    out = tool.invoke({"transcript_id": TID})
    assert out["lines"] == "[00:01] 화자 A: 에이전트를 만들려고" and "failed" not in out

    def missing(ref):
        raise ct.TranscriptUnavailable("없음")

    monkeypatch.setattr(ct, "fetch_transcript", missing)
    assert tool.invoke({"transcript_id": TID}) == {"error": "없음"}


def test_the_group_glossary_loads():
    g = tc.Glossary.load(DATA_DIR / "transcript_glossary.json")
    assert "에이전트" in g.terms and ("에이젠드", "에이전트") in g.replacements
    assert [len(w) for w, _ in g.replacements] == sorted((len(w) for w, _ in g.replacements), reverse=True)
