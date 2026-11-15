"""Test isolation: meeting database, outbound network, runtime state.

Tests never touch a configured MEETINGS_DATABASE_URL: it is cleared for every test. A test
that needs meeting records asks for `meeting_db`, which points both systems' stores at
MEETINGS_TEST_DATABASE_URL (a disposable Postgres 15+ database whose name contains "test";
the schemas `kelec` and `grp` are dropped and re-seeded for each test) and is skipped when
that variable is not set. Without a principal, runs see everything there (anonymous clearance 4).

Outbound network: a test may only open sockets to this machine (the test database, a local
server). Tests that really call a model or a website are marked `network` (tests/test_agents.py).

Runtime state: NEWS_STATE_DIR points at a temporary folder before the systems are imported, and
the session fails if a test run left anything new in a system's data/ folder.
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

os.environ["NEWS_STATE_DIR"] = tempfile.mkdtemp(prefix="news-state-test-")

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.systems.group import MEETING_STORE as GROUP_STORE  # noqa: E402
from app.systems.k_electronics import MEETING_STORE as K_STORE  # noqa: E402

STORES = (K_STORE, GROUP_STORE)
SYSTEMS_DIR = Path(__file__).resolve().parent.parent / "app" / "systems"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _data_files() -> set[str]:
    return {str(p.relative_to(SYSTEMS_DIR)) for p in SYSTEMS_DIR.glob("*/data/**/*")}


@pytest.fixture(scope="session", autouse=True)
def data_folders_unchanged():
    before = _data_files()
    yield
    added = sorted(_data_files() - before)
    assert not added, f"the test run wrote into a system's data folder: {added}"


def _host(address) -> str:
    return address[0] if isinstance(address, tuple) else str(address)


@pytest.fixture(autouse=True)
def own_news_state(tmp_path, monkeypatch):
    """Each test gets its own runtime state folder (nothing carries over between tests)."""
    monkeypatch.setenv("NEWS_STATE_DIR", str(tmp_path / "news-state"))


@pytest.fixture(autouse=True)
def no_outbound_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        yield
        return
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(address) not in LOCAL_HOSTS:
            raise OSError(f"test tried to reach the network: {address!r} (mark it `network` if it must)")
        return real_connect(self, address)

    def guarded_resolve(host, *args, **kwargs):
        if host not in LOCAL_HOSTS and host is not None:
            raise OSError(f"test tried to resolve {host!r} (mark it `network` if it must)")
        return real_resolve(host, *args, **kwargs)

    real_resolve = socket.getaddrinfo
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_resolve)
    yield


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
