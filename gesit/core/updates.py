"""Look up the newest published Gesit release on GitHub.

Only a read-only GET of the public "latest release" endpoint; nothing about the user or the
downloads is sent. The page that gets opened is rebuilt from the validated tag, never taken
from the response, so a tampered answer cannot point the user somewhere else.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from .. import __version__

REPO = "Rifal21/GesitDownloadManager"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/tag/"
_TAG_RE = re.compile(r"^v?\d+(\.\d+){0,3}$")


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Update:
    version: str   # "1.2.0"
    url: str       # release page
    notes: str     # release notes, plain text, shortened


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.3' -> (1, 2, 3). Pre-release tags such as '1.2.0-rc1' are not versions we offer."""
    text = (text or "").strip()
    if not _TAG_RE.match(text):
        return None
    return tuple(int(p) for p in text.lstrip("v").split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def check(current: str = __version__, proxy: str | None = None, timeout: float = 8.0,
          url: str | None = None) -> Update | None:
    """Return the newer release, or None when this is already the latest. Raises UpdateError."""
    url = url or os.environ.get("GESIT_UPDATE_URL") or API_URL
    kwargs: dict = dict(timeout=timeout, follow_redirects=True,
                        headers={"Accept": "application/vnd.github+json", "User-Agent": f"Gesit/{current}"})
    if proxy:
        kwargs["proxy"] = proxy
    try:
        with httpx.Client(**kwargs) as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        raise UpdateError(f"Could not reach GitHub: {e}") from e
    if r.status_code == 404:
        return None  # no release published yet
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered HTTP {r.status_code}")
    try:
        data = r.json()
        tag = str(data["tag_name"])
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateError("Unexpected answer from GitHub") from e
    if data.get("draft") or data.get("prerelease") or not is_newer(tag, current):
        return None
    notes = str(data.get("body") or "").strip()
    if len(notes) > 700:
        notes = notes[:700].rstrip() + "..."
    return Update(version=tag.lstrip("v"), url=RELEASE_PAGE + tag, notes=notes)
