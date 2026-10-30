"""People directory lookup — the registered email of a person named in meeting records.

The meeting store knows people by the names spoken in meetings. Their email is the one they
registered at login (Keycloak); the gateway, which has directory access, answers name lookups at
DIRECTORY_URL (Bearer INTERNAL_API_KEY). Unset → no lookup, and mail to that person waits for an
address. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("agents")


def lookup_email(name: str, *, client: httpx.Client | None = None) -> tuple[str, str | None] | None:
    """(email, keycloak sub) of the one enabled user with that name; None if nobody or several match."""
    url = os.environ.get("DIRECTORY_URL", "").strip()
    if not url or not name.strip():
        return None
    headers = {"Authorization": f"Bearer {os.environ.get('INTERNAL_API_KEY', '')}"}
    try:
        http = client or httpx.Client(timeout=5.0)
        try:
            res = http.get(url, params={"name": name.strip()}, headers=headers)
        finally:
            if client is None:
                http.close()
        res.raise_for_status()
        people = res.json().get("people") or []
    except (httpx.HTTPError, ValueError) as e:
        log.warning("directory lookup failed for %r: %s", name, e)
        return None
    if len(people) != 1 or not people[0].get("email"):
        return None
    return people[0]["email"], people[0].get("sub")
