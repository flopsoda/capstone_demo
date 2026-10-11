from app.events import AgentError, collect_events, emit, stream_run


def test_collect_events_captures_emitted_events_in_order():
    def work():
        emit({"type": "agent_start", "agent": "x", "request": "r"})
        emit({"type": "agent_end", "agent": "x", "response": "ok"})
        return "ok"

    result, events = collect_events(work)
    assert result == "ok"
    assert [e["type"] for e in events] == ["agent_start", "agent_end"]


def test_emit_outside_a_run_is_a_noop():
    emit({"type": "agent_start", "agent": "x", "request": "r"})  # must not raise


def test_stream_run_ends_with_done():
    def work():
        emit({"type": "agent_start", "agent": "x", "request": "r"})

    assert [e["type"] for e in stream_run(work)] == ["agent_start", "done"]


def test_stream_run_reports_agent_error_with_agent():
    def work():
        emit({"type": "agent_start", "agent": "meeting", "request": "r"})
        raise AgentError("meeting", "boom")

    events = list(stream_run(work))
    assert events[-1] == {"type": "error", "agent": "meeting", "message": "boom"}
