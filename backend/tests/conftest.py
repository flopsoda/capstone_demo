"""Meeting database for the tests.

Tests never touch a configured MEETINGS_DATABASE_URL: it is cleared for every test. A test
that needs meeting records asks for `meeting_db`, which points both systems' stores at
MEETINGS_TEST_DATABASE_URL (a disposable Postgres 15+ database whose name contains "test";
the schemas `kelec` and `grp` are dropped and re-seeded for each test) and is skipped when
that variable is not set. Without a principal, runs see everything there (anonymous clearance 4).
"""

import os

import psycopg
import pytest
from psycopg import sql

from app.systems.group import MEETING_STORE as GROUP_STORE
from app.systems.k_electronics import MEETING_STORE as K_STORE

STORES = (K_STORE, GROUP_STORE)


@pytest.fixture(autouse=True)
def no_configured_meeting_db(monkeypatch):
    monkeypatch.delenv("MEETINGS_DATABASE_URL", raising=False)
    for store in STORES:
        store.reset()
    yield
    for store in STORES:
        store.reset()


@pytest.fixture
def meeting_db(no_configured_meeting_db, monkeypatch):
    dsn = os.environ.get("MEETINGS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("MEETINGS_TEST_DATABASE_URL is not set (disposable Postgres for the meeting store tests)")
    with psycopg.connect(dsn, autocommit=True) as conn:
        name = conn.info.dbname
        if "test" not in name:
            pytest.fail(f"MEETINGS_TEST_DATABASE_URL points at '{name}'; the tests drop schemas, so use a database named *test*")
        for store in STORES:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(store.schema)))
    monkeypatch.setenv("MEETINGS_DATABASE_URL", dsn)
    monkeypatch.setenv("MEETINGS_ANONYMOUS_CLEARANCE", "4")
    return dsn
