"""Hourly sweep: re-check open de-list requests that need time to resolve.

Covers two kinds of open, unresolved `delist-request` issues (see
verify_delist.py for how each gets its initial label):

  OWNER path (`ownership-unconfirmed` or `delist-error`) -- re-checked every
  run; auto-closed after OWNER_STALE_HOURS with no resolution.

  THIRD-PARTY report path (`delist-report`) -- someone who isn't the plugin's
  owner flagged it as abandoned. Re-checked every run for `"delist": true`
  (or the repo going archived/gone), which auto-applies immediately -- that's
  legitimate proof-of-control regardless of who reported it. Otherwise waits
  up to REPORT_STALE_HOURS (7 days) for the actual owner to respond (a
  comment from them, matched by GitHub login against the repo owner, counts
  as a response either way -- agreement or dispute, either needs a human).
  No response in that window, or a response that doesn't resolve it either
  way, gets labeled `needs-manual-review` instead of being auto-closed --
  unlike the owner path, nobody here can unilaterally decide to drop a report
  someone else raised.

WHY THIS EXISTS: the interactive workflow (delist-verify.yml) only re-checks
on an issue edit or a `/recheck` comment -- both require the submitter to come
back to THIS repo and do something. But the actual fix for "unconfirmed" is
pushing `"delist": true` to THEIR OWN plugin repo, which has no webhook
connection here at all. Most submitters will just push that and consider
themselves done. This sweep is what actually catches that, with zero action
required from them: it re-fetches each plugin's pluginInfo.json fresh every
run, so a newly-pushed delist:true is picked up on the next hourly pass.

Usage: recheck_delist_issues.py --plugin-list pluginList.json
Reads GITHUB_REPOSITORY and GITHUB_TOKEN from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_plugin_schema as lib  # noqa: E402
from remove_delisted_plugin import remove_entry  # noqa: E402
from verify_delist import field, resolve_owner  # noqa: E402

API = "https://api.github.com"
UA = "fpp-data-plugin-ci"
OWNER_STALE_HOURS = 24
REPORT_STALE_HOURS = 24 * 7
MARKER = "<!-- delist-ownership-check -->"


def _req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


TARGET_LABELS = ("ownership-unconfirmed", "delist-error", "delist-report")


def list_candidates(repo: str, token: str) -> list[dict]:
    """Open delist-request issues carrying any of TARGET_LABELS."""
    seen: dict[int, dict] = {}
    for extra_label in TARGET_LABELS:
        page = 1
        while True:
            url = (f"{API}/repos/{repo}/issues?state=open"
                   f"&labels=delist-request,{extra_label}&per_page=100&page={page}")
            batch = _req("GET", url, token)
            if not batch:
                break
            for iss in batch:
                seen[iss["number"]] = iss
            if len(batch) < 100:
                break
            page += 1
    return list(seen.values())


def hours_open(issue: dict) -> float:
    created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600


def sticky_comment(repo: str, token: str, number: int, body: str):
    full = f"{MARKER}\n{body}"
    comments = _req("GET", f"{API}/repos/{repo}/issues/{number}/comments", token)
    existing = next((c for c in comments if MARKER in (c.get("body") or "")), None)
    if existing:
        _req("PATCH", f"{API}/repos/{repo}/issues/comments/{existing['id']}", token, {"body": full})
    else:
        _req("POST", f"{API}/repos/{repo}/issues/{number}/comments", token, {"body": full})


def swap_label(repo: str, token: str, number: int, want: str, all_verdict_labels: tuple[str, ...]):
    for l in all_verdict_labels:
        if l != want:
            try:
                _req("DELETE", f"{API}/repos/{repo}/issues/{number}/labels/{l}", token)
            except urllib.error.HTTPError:
                pass
    if want:
        _req("POST", f"{API}/repos/{repo}/issues/{number}/labels", token, {"labels": [want]})


VERDICT_LABELS = ("owner-verified", "ownership-unconfirmed", "delist-error",
                   "delist-report", "needs-manual-review", "delisted")


def apply_removal(plugin_list: str, repo_name: str, number: int, repo: str, token: str) -> tuple[str | None, str | None]:
    """Remove repo_name from plugin_list, commit+push, comment, label, close. (removed, error)."""
    with open(plugin_list, encoding="utf-8") as f:
        text = f.read()
    new_text, err = remove_entry(text, repo_name)
    if err:
        return None, err
    with open(plugin_list, "w", encoding="utf-8") as f:
        f.write(new_text)

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email",
                     "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", plugin_list], check=True)
    subprocess.run(["git", "commit", "-m",
                     f"Delist {repo_name} (verified request #{number}, hourly recheck)"], check=True)
    subprocess.run(["git", "push"], check=True)

    sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    _req("POST", f"{API}/repos/{repo}/issues/{number}/comments", token,
         {"body": f"✅ `\"delist\": true` detected on our hourly recheck — removed `{repo_name}` "
                  f"from `pluginList.json` (commit {sha})."})
    swap_label(repo, token, number, "delisted", VERDICT_LABELS)
    _req("PATCH", f"{API}/repos/{repo}/issues/{number}", token, {"state": "closed"})
    return repo_name, None


def close_stale_owner_path(repo: str, token: str, issue: dict, repo_name: str, err: str | None):
    """OWNER path only: nobody but the submitter has standing here, so a silent
    timeout can just close it -- there's no third party whose report would
    otherwise go unheard."""
    number = issue["number"]
    if err:
        body = (f"⏱️ It's been {OWNER_STALE_HOURS}+ hours and we still couldn't resolve `{repo_name}` "
                f"({err}) — closing this request. If removal is still needed, please open a new "
                f"de-list request with the correct repoName.")
    else:
        body = (f"⏱️ It's been {OWNER_STALE_HOURS}+ hours and we still don't see `\"delist\": true` in "
                f"`{repo_name}`'s `pluginInfo.json` — closing this request. If removal is still "
                f"needed, please open a new de-list request once that's pushed (or ask a maintainer "
                f"for help if you can't prove ownership another way).")
    sticky_comment(repo, token, number, body)
    swap_label(repo, token, number, "", VERDICT_LABELS)  # clear the verdict label, keep delist-request
    _req("PATCH", f"{API}/repos/{repo}/issues/{number}", token, {"state": "closed"})


def owner_has_commented(repo: str, token: str, number: int, author: str, owner: str) -> bool:
    """Has the plugin's actual owner (not the reporter) said anything on this issue?"""
    if not owner:
        return False
    comments = _req("GET", f"{API}/repos/{repo}/issues/{number}/comments", token)
    return any((c.get("user") or {}).get("login", "").lower() == owner.lower() for c in comments)


def flag_manual_review(repo: str, token: str, number: int, repo_name: str, owner_responded: bool):
    """THIRD-PARTY report path: never auto-closed -- the reporter isn't the one
    who gets to decide this doesn't matter, so a stuck report always ends up in
    front of a human instead of silently going away."""
    if owner_responded:
        body = (f"👤 The owner of `{repo_name}` has commented on this report. Flagging for the "
                f"FPP team to review and decide — a response (whether agreement or dispute) needs "
                f"a human call, not an automated one.")
    else:
        body = (f"⏱️ It's been {REPORT_STALE_HOURS // 24} days with no response from `{repo_name}`'s "
                f"owner. Flagging for the FPP team to review manually — third-party reports are "
                f"never auto-closed or auto-applied without either owner action or a maintainer "
                f"decision.")
    sticky_comment(repo, token, number, body)
    _req("POST", f"{API}/repos/{repo}/issues/{number}/labels", token,
         {"labels": ["needs-manual-review"]})  # additive: keep delist-report, don't swap it out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-list", default="pluginList.json")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo or not token:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")

    candidates = list_candidates(repo, token)
    if not candidates:
        print("No open de-list issues need rechecking -- nothing to do.")
        return

    print(f"Rechecking {len(candidates)} issue(s)...")
    for issue in candidates:
        number = issue["number"]
        labels = {l["name"] for l in issue.get("labels", [])}
        is_report = "delist-report" in labels

        # Already flagged for a human -- terminal state for this sweep, don't
        # re-process every hour/re-comment on it.
        if "needs-manual-review" in labels:
            print(f"  #{number}: already needs-manual-review, skipping")
            continue

        body = issue.get("body") or ""
        author = (issue.get("user") or {}).get("login", "")
        repo_name = lib.resolve_repo_name(field(body, "Plugin repoName") or field(body, "repoName"))
        if not repo_name:
            print(f"  #{number}: no repoName found in body, skipping")
            continue

        owner, repo_slug, gone, delist, err = resolve_owner(repo_name, args.plugin_list, token)
        # Third-party reports: author is the REPORTER, not the owner -- an
        # author==owner match would be meaningless (usually false, and if true
        # would mean the "third party" is actually the owner, which the form's
        # dropdown already lets them self-correct by picking "Yes" instead).
        # delist:true / gone are unconditional proof regardless of who reported it.
        if is_report:
            verified = (err is None) and (delist or gone)
        else:
            verified = (err is None) and (
                delist or gone or (author and owner and author.lower() == owner.lower())
            )

        if verified:
            print(f"  #{number} {repo_name}: now verified -- applying removal")
            removed, apply_err = apply_removal(args.plugin_list, repo_name, number, repo, token)
            if apply_err:
                print(f"    apply failed: {apply_err}")
            continue

        age = hours_open(issue)
        if is_report:
            responded = owner_has_commented(repo, token, number, author, owner or "")
            if responded:
                print(f"  #{number} {repo_name}: owner commented -- flagging for manual review")
                flag_manual_review(repo, token, number, repo_name, owner_responded=True)
            elif age >= REPORT_STALE_HOURS:
                print(f"  #{number} {repo_name}: no owner response after {age:.1f}h -- flagging for manual review")
                flag_manual_review(repo, token, number, repo_name, owner_responded=False)
            else:
                print(f"  #{number} {repo_name}: report still waiting on owner ({age:.1f}h old)")
        elif age >= OWNER_STALE_HOURS:
            print(f"  #{number} {repo_name}: unresolved after {age:.1f}h -- closing")
            close_stale_owner_path(repo, token, issue, repo_name, err)
        else:
            print(f"  #{number} {repo_name}: still unresolved ({age:.1f}h old)")


if __name__ == "__main__":
    main()
