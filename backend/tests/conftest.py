"""Test isolation: meeting database, outbound network, system data folders.

Tests never touch a configured MEETINGS_DATABASE_URL: it is cleared for every test. A test
that needs meeting records asks for `meeting_db`, which points both systems' stores at
MEETINGS_TEST_DATABASE_URL (a disposable Postgres 15+ database whose name contains "test";
the schemas `kelec` and `grp` are dropped and re-seeded for each test) and is skipped when
that variable is not set. Without a principal, runs see everything there (anonymous clearance 4).

Outbound network: a test may only open sockets to this machine (the test database, a local
server). Tests that really call a model or a website are marked `network` (tests/test_agents.py).

News: the systems' news stores need a database (`news_db`: the test database, both systems' news
schemas dropped, so each is seeded again from its data/news.json on first use). Tests never poll the
real publisher feeds (news/feeds.json reads as empty); a test that wants feeds passes its own. The
embedding model is off (NEWS_EMBED_MODEL=none: keyword search); a test that wants semantic search
gives the pipeline its own embedder.
The session fails if a test run left anything new in a system's data/ folder.
"""

import os
import socket
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from app.library.workflows.news.store import NewsStore
from app.systems.group import MEETING_STORE as GROUP_STORE, NEWS_STORE as GROUP_NEWS
from app.systems.k_electronics import MEETING_STORE as K_STORE, NEWS_STORE as K_NEWS

STORES = (K_STORE, GROUP_STORE)
NEWS_STORES = (K_NEWS, GROUP_NEWS)
TEST_NEWS_SCHEMA = "test_news"
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
def no_embedding_model(monkeypatch):
    monkeypatch.setenv("NEWS_EMBED_MODEL", "none")


@pytest.fixture(autouse=True)
def no_publisher_feeds(monkeypatch):
    from app.library.tools import run_company_risk_news_analysis as risk_tool, run_news_briefing as briefing_tool
    from app.library.workflows.news.feeds import DEFAULT_FEEDS, load_feeds

    def tests_feeds(path=DEFAULT_FEEDS):
        return [] if path == DEFAULT_FEEDS else load_feeds(path)

    for module in (briefing_tool, risk_tool):
        monkeypatch.setattr(module, "load_feeds", tests_feeds)


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
    monkeypatch.delenv("NEWS_DATABASE_URL", raising=False)
    for store in (*STORES, *NEWS_STORES):
        store.reset()
    yield
    for store in (*STORES, *NEWS_STORES):
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


def _test_dsn() -> str:
    dsn = os.environ.get("MEETINGS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("MEETINGS_TEST_DATABASE_URL is not set (disposable Postgres for the store tests)")
    return dsn


@pytest.fixture
def news_db(no_configured_meeting_db, monkeypatch):
    """Both systems' news stores (seeded again from their data/news.json on first use) and a
    NewsStore("test_news") for workflow tests, on a fresh test database."""
    dsn = _test_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        if "test" not in conn.info.dbname:
            pytest.fail("MEETINGS_TEST_DATABASE_URL must name a database called *test*")
        for schema in (*(s.schema for s in NEWS_STORES), TEST_NEWS_SCHEMA):
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    monkeypatch.setenv("NEWS_DATABASE_URL", dsn)
    return NewsStore(TEST_NEWS_SCHEMA)
