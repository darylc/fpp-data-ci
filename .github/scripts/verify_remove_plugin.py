"""Verify that a Request Plugin Removal issue comes from the plugin's owner.

Runs when a `removal-request` issue is opened/edited. GitHub has already
AUTHENTICATED the issue author, so `ISSUE_AUTHOR` is their real login — the job
is only to decide whether that login owns the named plugin's repo.

Verdicts:
  verified   — issue author == the repo owner parsed from srcURL (personal repo),
               OR the repo is archived / gone (nothing to protect).
  unconfirmed— repo is org-owned or the author isn't the owner; GitHub won't tell
               our CI their write access, so ask for proof-of-control:
               set `"delist": true` in the plugin's pluginInfo.json (only someone
               with write access can), which also becomes the machine signal.
  not_found  — the named repoName isn't in pluginList.json at all. There's no
               listing to remove, so this is auto-closed rather than left for a
               maintainer (see the workflow's "Auto-close: not listed" step).
  error      — couldn't resolve the plugin / repo for some other reason. Two
               flavors: a transient one (couldn't fetch the listed
               pluginInfo.json right now — network blip, rate limit) that
               just needs a `/recheck` later and doesn't need a maintainer at
               all; and a structural one (can't determine owner/repo from the
               listing at all) that does.

Reads ISSUE_AUTHOR and ISSUE_BODY from the environment. Writes a Markdown comment
to --output and the verdict label to $GITHUB_OUTPUT (key `verdict`).
"""

from __future__ import annotations

import argparse
import os
import re

import lib_plugin_schema as lib


field = lib.field  # moved into lib_plugin_schema.py — shared with scan_submission.py


def find_duplicate_removal_issues(repo_name: str, current_issue: str, gh_repo: str, token) -> list[int]:
    """Other OPEN removal-request issues asking about the same repoName (case-
    insensitive, matching resolve_owner()'s own casing tolerance)."""
    dupes = []
    for issue in lib.list_open_issues(gh_repo, "removal-request", token):
        if str(issue.get("number")) == str(current_issue):
            continue
        other_name = resolve_repo_name_field(issue.get("body") or "")
        if other_name and other_name.lower() == repo_name.lower():
            dupes.append(issue["number"])
    return dupes


def resolve_repo_name_field(body: str) -> str:
    return lib.resolve_repo_name(field(body, "Plugin repoName") or field(body, "repoName"))


def resolve_owner(repo_name: str, plugin_list_path: str, token):
    """(owner, repo, archived_or_missing, delist_flag, error, not_found, transient) for a listed plugin.

    `transient` marks the one failure mode that's worth telling the reporter to
    just `/recheck` later instead of waiting on a maintainer: the listed
    pluginInfo.json URL didn't fetch. That's a network/rate-limit blip as often
    as a real dead link, and either way the fix is the same non-human action —
    try again.

    Matches case-insensitively: GitHub repo names are case-insensitive in URLs,
    but a plugin's declared repoName (what pluginList.json stores) doesn't
    always match its repo's URL casing byte-for-byte (e.g. "fpp-PulseMesh" vs.
    the repo slug "fpp-pulsemesh") -- an exact match would wrongly report a
    listed plugin as not found.

    `not_found` is split out from the other error cases: it means the repoName
    genuinely isn't listed (nothing to remove), vs. a listed entry we merely
    failed to resolve (network/data problem) — callers auto-close the former
    and leave the latter for a maintainer.
    """
    for entry in lib.load_pluginlist(plugin_list_path):
        if entry and entry[0].lower() == repo_name.lower():
            info_url = entry[1] if len(entry) > 1 else ""
            info, err = lib.fetch_json(info_url)
            if err:
                return None, None, False, False, f"couldn't fetch pluginInfo.json: {err}", False, True
            info = info or {}
            # Proof-of-control: an author with write access set delist:true in their
            # OWN pluginInfo.json ("delist" is pluginInfo.schema.json's actual field
            # name — an external contract, not renamed here). Only a writer could, so
            # it proves control even for an org repo — and it doubles as the
            # machine-readable removal signal.
            delist = bool(info.get("delist"))
            # owner/repo primarily from pluginList.json's OWN infoURL (entry[1]),
            # not pluginInfo.json's self-declared srcURL: we just fetched `info`
            # from that URL successfully, so it's a raw.githubusercontent.com/
            # {owner}/{repo}/... link we already trust — no dependency on the
            # plugin author having filled in srcURL correctly. srcURL is only a
            # fallback for the rare case pluginInfo.json is hosted somewhere
            # parse_raw_github_repo can't parse (custom domain, etc.).
            src = lib.parse_raw_github_repo(info_url) or lib.parse_github_repo(info.get("srcURL", "") or "")
            if not src:
                return None, None, False, delist, "couldn't determine the plugin's GitHub owner/repo from its listing", False, False
            owner, repo = src
            data, gherr = lib.gh_get_repo(owner, repo, token)
            # Only "gone" on a definitive 404 or explicit archived flag — NOT on a
            # transient error (rate limit / network), which would falsely verify.
            missing = data is None and bool(gherr) and "404" in gherr
            archived = bool(data and data.get("archived"))
            return owner, repo, (missing or archived), delist, None, False, False
    return None, None, False, False, f"'{repo_name}' is not in pluginList.json", True, False


def field_block(repo_name: str, owner: str | None = None, repo: str | None = None) -> str:
    """`**Field:** value` lines for the top of the verdict comment — same style as
    scan_submission's comment, so both the submission and removal flows read consistently."""
    lines = [f"**Plugin:** {repo_name}"]
    if owner and repo:
        lines.append(f"**Repo:** https://github.com/{owner}/{repo}")
        lines.append(f"**Owner:** `{owner}`")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-list", default="pluginList.json")
    ap.add_argument("--output", default="removal-verify.md")
    ap.add_argument("--gh-repo", default=None, help="owner/repo this issue lives in, for duplicate-request detection")
    ap.add_argument("--current-issue", default=None)
    args = ap.parse_args()

    author = (os.environ.get("ISSUE_AUTHOR") or "").strip()
    body = os.environ.get("ISSUE_BODY") or ""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo_name = resolve_repo_name_field(body)
    # Third-party reports (submitter isn't the author) can never prove ownership,
    # so they skip straight to human review instead of being asked for delist:true
    # proof-of-control they have no way to provide.
    is_third_party = field(body, "Are you the plugin's author/maintainer?").strip().lower().startswith("no")

    if not repo_name:
        verdict, msg = "error", "Could not read a **Plugin repoName** from the form."
    elif is_third_party:
        owner, repo, gone, delist, err, not_found, transient = resolve_owner(repo_name, args.plugin_list, token)
        if not_found:
            verdict, msg = "not_found", (
                f"{field_block(repo_name)}\n\n"
                f"`{repo_name}` is not currently listed in `pluginList.json`, so there's nothing "
                f"to remove.")
        elif transient:
            verdict, msg = "error", (
                f"{field_block(repo_name)}\n\n"
                f"Could not resolve this report right now: {err}\n\n"
                f"This is likely transient — comment `/recheck` in a bit and we'll try again.")
        elif err:
            verdict, msg = "error", f"{field_block(repo_name)}\n\nCould not resolve this report: {err}"
        elif delist or gone:
            # Owner already proved control (or the repo's gone) -- no need to wait
            # on anyone, this is functionally the same as an owner-verified request.
            verdict, msg = "verified", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"✅ Third-party report, but " +
                (f"`pluginInfo.json` already declares `\"delist\": true`"
                 if delist else "the repo is archived or no longer reachable") +
                f" — applying immediately, no waiting period needed.")
        else:
            verdict, msg = "report", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"📋 Third-party report received — thanks for flagging it. "
                f"This is **not** applied automatically; only the plugin's own owner can do that.\n\n"
                f"{lib.owner_ref(owner)} — if you'd like this plugin removed, open your own removal "
                f"request or set `\"delist\": true` in your `pluginInfo.json` (we'll detect it "
                f"automatically, no need to comment). If you disagree with removal, please say so "
                f"in a comment here. If there's no response within 7 days, this will be flagged for "
                f"the FPP team to review manually.")
    else:
        owner, repo, gone, delist, err, not_found, transient = resolve_owner(repo_name, args.plugin_list, token)
        if not_found:
            verdict, msg = "not_found", (
                f"{field_block(repo_name)}\n\n"
                f"`{repo_name}` is not currently listed in `pluginList.json`, so there's nothing "
                f"to remove.")
        elif transient:
            verdict, msg = "error", (
                f"{field_block(repo_name)}\n\n"
                f"Could not verify this right now: {err}\n\n"
                f"This is likely transient — comment `/recheck` in a bit and we'll try again. No "
                f"need to open a new issue.")
        elif err:
            verdict, msg = "error", (
                f"{field_block(repo_name)}\n\n"
                f"Could not verify this: {err}\n\n"
                f"Fixed the repoName (typo, casing, etc.)? Edit this issue's description with the "
                f"correct value and we'll automatically re-check — no need to open a new issue.")
        elif delist:
            verdict, msg = "verified", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"✅ Proof-of-control confirmed: `pluginInfo.json` declares `\"delist\": true` — "
                f"only someone with write access could set that.")
        elif gone:
            verdict, msg = "verified", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"Archived or no longer reachable — removal is justified regardless of who asked.")
        elif author and owner and author.lower() == owner.lower():
            verdict, msg = "verified", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"✅ Ownership confirmed: **@{author}** is the owner.")
        else:
            verdict, msg = "unconfirmed", (
                f"{field_block(repo_name, owner, repo)}\n\n"
                f"⚠️ **@{author}** is not the direct owner (it may be org-owned). GitHub won't "
                f"confirm your write access to us, so please **prove control**: set "
                f"`\"delist\": true` in the plugin's `pluginInfo.json` and push it (only someone "
                f"with write access can).\n\n"
                f"Once it's pushed, comment `/recheck` on this issue and we'll re-verify automatically "
                f"— no need to open a new issue.")

    # Other OPEN removal requests for the same plugin — independent of verdict, since
    # even a not_found/error request can still duplicate a genuinely pending one.
    dupes: list[int] = []
    if repo_name and args.gh_repo and args.current_issue:
        dupes = find_duplicate_removal_issues(repo_name, args.current_issue, args.gh_repo, token)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"### Plugin removal ownership check — `{verdict}`\n\n{msg}\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"verdict={verdict}\n")
            f.write(f"repo_name={repo_name}\n")
            f.write(f"duplicate_issues={','.join(str(n) for n in dupes)}\n")
    print(f"{verdict}: {msg}")


if __name__ == "__main__":
    main()
