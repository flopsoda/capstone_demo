"""correct_transcript — The Group's instance: its meeting store, its glossary (data/transcript_glossary.json), its meeting agent.

TRANSCRIPT_CORRECTION.prepare runs before the meeting agent reads a direct request (app.py)."""

from __future__ import annotations

from ....library.tools.correct_transcript import make_transcript_correction
from .. import DATA_DIR, MEETING_STORE

TRANSCRIPT_CORRECTION = make_transcript_correction(MEETING_STORE, DATA_DIR / "transcript_glossary.json", agent="meeting")
correct_transcript = TRANSCRIPT_CORRECTION.tool
