"""Compliance scan for a brand-new plugin submission issue.

Reuses the same checks as the retroactive fpp<major> campaign (lint_plugin.py, the
pluginInfo.json schema check, and the repo-metadata checks — all shared via
lib_plugin_schema.py so the two scanners can't quietly drift apart), but gates HARDER:
a plugin that's already listed keeps working if it picks up a BEST_PRACTICE finding
after the fact — campaign findings are advisory, they never block a listing or trigger
its removal on their own. A plugin asking to be listed for the FIRST time has no such
grandfathering: here, BEST_PRACTICE findings block same as BLOCKER, not just advisory.
OPTIONAL stays advisory in both cases (LICENSE/README/icon/bugURL are nice-to-have,
not a gate).

repoName and the github.com repo are never taken from the submitter — both are derived
here from pluginInfo.json itself (repoName is a field in the JSON; the repo comes from
its srcURL, falling back to the raw.githubusercontent.com pluginInfo-url). There is
nothing left for a submitter-supplied copy to mismatch against, so unlike the campaign
scanner there is no repo-name-mismatch check here.

Usage:
  scan_submission.py --plugininfo-url <raw pluginInfo.json URL> \
      --schema .github/schema/pluginInfo.schema.json --out result.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_plugin_schema import (  # noqa: E402
    fetch_json,
    gh_get_repo,
    parse_github_repo,
    parse_raw_github_repo,
    repo_metadata_findings,
    schema_validation_error,
)
from lint_plugin import lint_plugin_dir, BLOCKER, BEST_PRACTICE, OPTIONAL  # noqa: E402

CLONE_TIMEOUT = 60  # seconds


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
    ap.add_argument("--schema", required=True)
    ap.add_argument("--reporter", default=None, help="GitHub login of the issue's original reporter")
    ap.add_argument("--issue-number", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    findings = []  # (severity, code, message)

    info, err = fetch_json(args.plugininfo_url)
    if err:
        findings.append((BLOCKER, "plugininfo-fetch", f"cannot fetch pluginInfo.json: {err}"))
        info = {}

    if info:
        with open(args.schema, encoding="utf-8") as f:
            schema = json.load(f)
        schema_err = schema_validation_error(info, schema)
        if schema_err:
            findings.append((BLOCKER, "schema", schema_err))

    # --- repo metadata (archived / issues-disabled / bugURL) --------------------
    gh = parse_github_repo(info.get("srcURL", "") or "") if info else None
    gh = gh or parse_raw_github_repo(args.plugininfo_url)
    if gh:
        owner, repo = gh
        meta, _ = gh_get_repo(owner, repo, token)
        if meta:
            findings.extend(repo_metadata_findings(meta, (info or {}).get("bugURL", "")))

    # --- ownership: submitter must own the repo, or prove write access ---------
    # Unlike removal (verify_remove_plugin.py), a submission had NO ownership check
    # at all until now — anyone could list anyone else's plugin. Mirrors removal's
    # "delist": true proof-of-control trick: only someone with push access could add
    # a specific string to their OWN pluginInfo.json. The token is tied to this one
    # issue (not a permanent flag like "delist") so an old, already-approved
    # submission's public token can't be replayed as proof for a different request.
    owner_confirmed = True
    if gh and args.reporter and gh[0].lower() != args.reporter.lower():
        expected = f"fpp-{args.issue_number}"
        got = str((info or {}).get("submissionToken", "")).strip()
        if got != expected:
            owner_confirmed = False
            findings.append((BLOCKER, "owner-unconfirmed",
                f"submitter @{args.reporter} does not match `{gh[0]}`, this repo's registered owner "
                f"(from srcURL). Add `\"submissionToken\": \"{expected}\"` to your pluginInfo.json and "
                f"comment `/recheck` to prove you have write access here — or, if you're submitting on "
                f"the owner's behalf with their consent, comment `/submit` instead to flag this for a "
                f"maintainer's judgement rather than auto-verifying."))

    # repoName always comes from pluginInfo.json itself, falling back to the github.com
    # repo slug (from srcURL/plugininfo-url) only if the JSON omits it — schema
    # validation above already blocks a submission with no repoName at all, so this
    # fallback just keeps clone/lint working in that already-failing case too.
    repo_name = (info or {}).get("repoName") or (gh[1] if gh else None)

    # --- clone + static lint --------------------------------------------------
    linted = False
    if gh:
        owner, repo = gh
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, repo)
            clone_err = clone_repo(owner, repo, dest)
            if clone_err:
                findings.append((BLOCKER, "clone-failed", clone_err))
            else:
                linted = True
                for f in lint_plugin_dir(dest, repo_name or repo, info=info):
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
        "owner": gh[0] if gh else None,   # registered owner from srcURL — may differ from the submitter
        "owner_confirmed": owner_confirmed,
        "repo_name": repo_name,
        "repo_url": f"https://github.com/{gh[0]}/{gh[1]}" if gh else None,
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
