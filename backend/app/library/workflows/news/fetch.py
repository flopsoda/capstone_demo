"""Fetching publisher pages politely: our own User-Agent, robots.txt (RFC 9309 via protego), a
timeout, a size cap and a small per-host gap. Nothing here works around an access limit: a page
robots.txt disallows is not fetched, and an error, a bot challenge or a login/paywall page is a
failure the caller records — never retried in another guise.

`transport` is the one network call (url, headers) -> (status, final url, content-type, body);
tests pass a fake one.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from protego import Protego

USER_AGENT = "CapstoneNewsAgent/0.1 (+non-commercial university capstone project)"
ROBOTS_TOKEN = "CapstoneNewsAgent"
MAX_BYTES = 3_000_000
ROBOTS_TTL = 6 * 3600  # seconds a host's robots.txt is reused

Transport = Callable[[str, dict[str, str]], tuple[int, str, str, bytes]]

# Bot-challenge / interstitial pages answer 200 with no article in them.
_CHALLENGE = (b"Just a moment...", b"cf-browser-verification", b"challenges.cloudflare.com", b"Attention Required!")


class FetchError(Exception):
    """`status` is what gets recorded on the article: robots_disallowed | http_<code> | blocked | timeout | error."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status} {detail}".strip())
        self.status = status


@dataclass
class Page:
    url: str  # after redirects
    content_type: str
    body: bytes


def urllib_transport(timeout: float) -> Transport:
    def call(url: str, headers: dict[str, str]) -> tuple[int, str, str, bytes]:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.geturl(), resp.headers.get("Content-Type", ""), resp.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            return e.code, url, e.headers.get("Content-Type", "") if e.headers else "", b""

    return call


class Fetcher:
    def __init__(self, transport: Transport | None = None, *, timeout: float = 8.0, host_gap: float = 0.3):
        self.transport = transport or urllib_transport(timeout)
        self.host_gap = host_gap
        self._robots: dict[str, tuple[float, Protego | None]] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _call(self, url: str) -> tuple[int, str, str, bytes]:
        host = urllib.parse.urlsplit(url).netloc
        with self._host_lock(host):  # one request at a time per host, a small gap between them
            wait = self._last.get(host, 0) + self.host_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return self.transport(url, {"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.5"})
            except TimeoutError as e:
                raise FetchError("timeout", str(e)) from e
            except (urllib.error.URLError, OSError, ValueError) as e:
                reason = getattr(e, "reason", e)
                raise FetchError("timeout" if isinstance(reason, TimeoutError) else "error", str(reason)[:120]) from e
            finally:
                self._last[host] = time.monotonic()

    def _rules(self, url: str) -> Protego | None:
        """None = everything disallowed (robots.txt unreachable or a server error, RFC 9309 §2.3.1.4);
        a 4xx robots.txt means no rules (§2.3.1.3)."""
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached and time.monotonic() - cached[0] < ROBOTS_TTL:
            return cached[1]
        try:
            status, _, _, body = self._call(origin + "/robots.txt")
        except FetchError:
            rules = None
        else:
            if status == 200 and not any(c in body[:4000] for c in _CHALLENGE):
                rules = Protego.parse(body.decode("utf-8", errors="replace"))
            elif 400 <= status < 500:
                rules = Protego.parse("")
            else:
                rules = None
        self._robots[origin] = (time.monotonic(), rules)
        return rules

    def allowed(self, url: str) -> bool:
        rules = self._rules(url)
        return rules is not None and rules.can_fetch(url, ROBOTS_TOKEN)

    def get(self, url: str) -> Page:
        if not self.allowed(url):
            raise FetchError("robots_disallowed")
        status, final, ctype, body = self._call(url)
        if status in (401, 402, 403, 407, 429, 451):
            raise FetchError("blocked", f"http {status}")
        if status != 200:
            raise FetchError(f"http_{status}")
        if len(body) > MAX_BYTES:
            raise FetchError("error", "page too large")
        if any(c in body[:20000] for c in _CHALLENGE):
            raise FetchError("blocked", "bot challenge page")
        if final != url and not self.allowed(final):  # a redirect does not get around robots.txt
            raise FetchError("robots_disallowed", "after redirect")
        return Page(final, ctype, body)
