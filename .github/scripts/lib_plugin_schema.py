"""Helpers shared by the plugin CI scripts.

Kept dependency-light on purpose: only the standard library + `jsonschema`
(installed by the workflow). Network calls use urllib with short timeouts so a
slow/dead host can't hang the CI job.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

import jsonschema

USER_AGENT = "fpp-data-plugin-ci"
HTTP_TIMEOUT = 15  # seconds — generous for CI, still bounded

# The rest of this campaign's CI NEVER @-mentions an author (see sync_issues.py,
# campaign_scan.py) -- bulk scans pinging authors would be spam. The removal-report
# flow (Request Plugin Removal, third-party path) is a deliberate, narrow exception:
# notifying an owner that THEIR OWN plugin was reported as abandoned is closer to
# "you should know about this" than a bulk scan is. Held off for now (plain text, no
# real notification) until release; flip this one flag then -- every caller goes
# through owner_ref() below.
MENTION_OWNER = False


def owner_ref(login: str) -> str:
    """A plugin owner's GitHub login, formatted per MENTION_OWNER.

    True: a real "@login" -- GitHub sends them a notification.
    False (default): backtick-wrapped plain text -- same convention the rest of
    this repo's CI uses ("no leading @, so nobody's pinged").
    """
    return f"@{login}" if MENTION_OWNER else f"`{login}`"


def fetch_json(url: str) -> tuple[Optional[Any], Optional[str]]:
    """GET a URL and parse JSON. Returns (data, error). One is always None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {url}"
    except Exception as e:  # noqa: BLE001 — surface any network/parse issue to the report
        return None, f"could not fetch {url}: {e}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON at {url}: {e}"


def resolve_repo_name(value: str) -> str:
    """Accept a bare repoName OR a GitHub URL and return just the repo name.

    Submitters of the Request Plugin Removal Issue Form often paste a URL into the "Plugin
    repoName" field instead of the bare name FPP actually stores in
    pluginList.json — a repo page (`github.com/<owner>/<repo>`, with or without
    `.git`, `/issues`, `/blob/<branch>/pluginInfo.json`, ...) or a raw file URL
    (`raw.githubusercontent.com/<owner>/<repo>/<branch>/pluginInfo.json`). Be
    forgiving rather than failing the request outright: `repoName` is required
    (by CONTRIBUTING.md) to match the GitHub repo name, so the repo segment of
    either URL shape IS the repoName.
    """
    v = (value or "").strip()
    if not v or ("github.com" not in v and "githubusercontent.com" not in v):
        return v
    try:
        from urllib.parse import urlparse

        u = urlparse(v if "://" in v else "https://" + v)
    except Exception:  # noqa: BLE001
        return v
    if not u.hostname or ("github.com" not in u.hostname and "githubusercontent.com" not in u.hostname):
        return v
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return v
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo


def parse_github_repo(url: str) -> Optional[tuple[str, str]]:
    """Return (owner, repo) for a github.com URL, else None.

    Handles https://github.com/owner/repo(.git)(/issues)(/...) forms.
    """
    try:
        from urllib.parse import urlparse

        u = urlparse(url)
    except Exception:  # noqa: BLE001
        return None
    if u.hostname not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def parse_raw_github_repo(url: str) -> Optional[tuple[str, str]]:
    """(owner, repo) from a raw.githubusercontent.com file URL, else None.

    Complements parse_github_repo() above, which only handles github.com repo pages —
    a pluginInfo.json URL is a raw.githubusercontent.com file URL instead, and is
    sometimes the only URL a caller has (e.g. a submission with no srcURL yet).
    """
    m = re.match(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", url or "")
    return (m.group(1), m.group(2)) if m else None


def schema_validation_error(info: dict, schema: dict) -> Optional[str]:
    """Validate pluginInfo.json against the schema. Returns a message, or None if valid.

    Severity-free on purpose: validate_pluginlist.py (ERROR/WARNING, downgraded for
    pre-existing entries not touched by a PR) and the campaign/submission scanners
    (BLOCKER/BEST_PRACTICE/OPTIONAL) each wrap this in their own severity model rather
    than sharing one — the two vocabularies don't map onto each other cleanly.
    """
    try:
        jsonschema.validate(info, schema)
        return None
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        return f"pluginInfo.json fails schema at `{loc}`: {e.message}"


def repo_metadata_findings(meta: dict, bug_url: str) -> list[tuple[str, str, str]]:
    """archived / issues-disabled / bugURL findings from a GitHub API repo response.

    Pure (no network) — the caller already has `meta` from gh_get_repo(). Returns
    (severity, code, message) tuples using the same string severities as lint_plugin.py's
    BLOCKER/BEST_PRACTICE/OPTIONAL ("blocker"/"best-practice"/"optional"), so callers can
    append these directly alongside lint_plugin_dir()'s findings without translation.
    """
    out: list[tuple[str, str, str]] = []
    if not meta:
        return out
    if meta.get("archived"):
        out.append(("best-practice", "archived", "source repo is archived"))
    has_bug = bool(parse_github_repo(bug_url or ""))
    # An archived repo is read-only regardless of what has_issues says, so archiving
    # always implies issues-disabled even if the flag wasn't flipped.
    if meta.get("has_issues") is False or meta.get("archived"):
        out.append(("blocker", "issues-disabled",
                     "GitHub Issues are disabled — users can't report bugs and we can't reach you there"))
    elif not has_bug:
        out.append(("optional", "bugurl", "no bugURL set (Report-a-Bug link)"))
    return out


def gh_get_repo(owner: str, repo: str, token: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    """GET /repos/{owner}/{repo} from the GitHub API. Token lifts the rate limit to 5000/hr."""
    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def load_categories(path: str) -> set[str]:
    """Load the allowed category names from pluginCategories.json.

    `name` is the short name pluginList.json stores and matches on; `longName`
    is the descriptive form used only for display.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {c["name"] for c in data.get("categories", [])}


def load_pluginlist(path: str) -> list[list]:
    """Load pluginList.json and return its `pluginList` array (raises on parse error)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["pluginList"]
