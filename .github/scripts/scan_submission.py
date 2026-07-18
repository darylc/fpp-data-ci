"""Compliance scan for a brand-new plugin submission issue.

Reuses the same checks as the retroactive fpp<major> campaign (lint_plugin.py +
the pluginInfo.json schema in validate_pluginlist.py), but gates HARDER: a plugin
that's already listed keeps working if it picks up a BEST_PRACTICE finding after
the fact — campaign findings are advisory, they never block or delist on their
own. A plugin asking to be listed for the FIRST time has no such grandfathering:
here, BEST_PRACTICE findings block same as BLOCKER, not just advisory. OPTIONAL
stays advisory in both cases (LICENSE/README/bugURL are nice-to-have, not a gate).

Usage:
  scan_submission.py --plugininfo-url <raw pluginInfo.json URL> --repo-name <repoName> \
      --schema .github/schema/pluginInfo.schema.json --out result.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import jsonschema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_plugin_schema import fetch_json, parse_github_repo  # noqa: E402
from lint_plugin import lint_plugin_dir, BLOCKER, BEST_PRACTICE, OPTIONAL  # noqa: E402

CLONE_TIMEOUT = 60  # seconds


def parse_raw_github_repo(url: str):
    """(owner, repo) from a raw.githubusercontent.com pluginInfo.json URL, else None."""
    m = re.match(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", url or "")
    return (m.group(1), m.group(2)) if m else None


def clone_repo(owner: str, repo: str, dest: str) -> str | None:
    """Shallow-clone into dest. Returns an error string, or None on success."""
    url = f"https://github.com/{owner}/{repo}.git"
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, dest],
            capture_output=True, text=True, timeout=CLONE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"git clone of {owner}/{repo} timed out after {CLONE_TIMEOUT}s"
    if proc.returncode != 0:
        return f"git clone of {owner}/{repo} failed: {proc.stderr.strip()[:300]}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugininfo-url", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    findings = []  # (severity, code, message)

    info, err = fetch_json(args.plugininfo_url)
    if err:
        findings.append((BLOCKER, "plugininfo-fetch", f"cannot fetch pluginInfo.json: {err}"))
        info = {}

    if info:
        with open(args.schema, encoding="utf-8") as f:
            schema = json.load(f)
        try:
            jsonschema.validate(info, schema)
        except jsonschema.ValidationError as e:
            loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
            findings.append((BLOCKER, "schema", f"pluginInfo.json fails schema at `{loc}`: {e.message}"))

        if args.repo_name and info.get("repoName") and info["repoName"] != args.repo_name:
            findings.append((BLOCKER, "repo-name-mismatch",
                              f"pluginInfo.json repoName '{info['repoName']}' does not match "
                              f"the submitted repoName '{args.repo_name}'"))

    # --- clone + static lint --------------------------------------------------
    linted = False
    src = info.get("srcURL") if info else None
    gh = parse_github_repo(src or "") or parse_raw_github_repo(args.plugininfo_url)
    if gh:
        owner, repo = gh
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, repo)
            clone_err = clone_repo(owner, repo, dest)
            if clone_err:
                findings.append((BLOCKER, "clone-failed", clone_err))
            else:
                linted = True
                for f in lint_plugin_dir(dest, args.repo_name or repo):
                    findings.append((f.severity, f.code, f.message))
    else:
        findings.append((BLOCKER, "no-repo-url",
                          "could not determine a github.com repo from srcURL or the pluginInfo.json URL"))

    # --- verdict ---------------------------------------------------------------
    # Stricter than the campaign: BEST_PRACTICE blocks a new submission, not just BLOCKER.
    blocking = [f for f in findings if f[0] in (BLOCKER, BEST_PRACTICE)]
    advisory = [f for f in findings if f[0] == OPTIONAL]

    result = {
        "pass": not blocking,
        "linted": linted,
        "findings": [{"severity": s, "code": c, "message": m} for s, c, m in findings],
        "num_blocking": len(blocking),
        "num_advisory": len(advisory),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"{'PASS' if result['pass'] else 'FAIL'} — {len(blocking)} blocking, {len(advisory)} advisory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
