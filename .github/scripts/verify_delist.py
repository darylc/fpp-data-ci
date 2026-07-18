"""Verify that a de-list request comes from the plugin's owner.

Runs when a `delist-request` issue is opened/edited. GitHub has already
AUTHENTICATED the issue author, so `ISSUE_AUTHOR` is their real login — the job
is only to decide whether that login owns the named plugin's repo.

Verdicts:
  verified   — issue author == the repo owner parsed from srcURL (personal repo),
               OR the repo is archived / gone (nothing to protect).
  unconfirmed— repo is org-owned or the author isn't the owner; GitHub won't tell
               our CI their write access, so ask for proof-of-control:
               set `"delist": true` in the plugin's pluginInfo.json (only someone
               with write access can), which also becomes the machine signal.
  error      — couldn't resolve the plugin / repo.

Reads ISSUE_AUTHOR and ISSUE_BODY from the environment. Writes a Markdown comment
to --output and the verdict label to $GITHUB_OUTPUT (key `verdict`).
"""

from __future__ import annotations

import argparse
import os
import re

import lib_plugin_schema as lib


def field(body: str, label: str) -> str:
    """Value under a GitHub issue-form '### <label>' heading (first non-empty line)."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lstrip("#").strip().lower() == label.lower():
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith("#"):
                    return s
    return ""


def resolve_owner(repo_name: str, plugin_list_path: str, token):
    """(owner, repo, archived_or_missing, delist_flag, error) for a listed plugin."""
    for entry in lib.load_pluginlist(plugin_list_path):
        if entry and entry[0] == repo_name:
            info_url = entry[1] if len(entry) > 1 else ""
            info, err = lib.fetch_json(info_url)
            if err:
                return None, None, False, False, f"couldn't fetch pluginInfo.json: {err}"
            info = info or {}
            # Proof-of-control: an author with write access set delist:true in their
            # OWN pluginInfo.json. Only a writer could, so it proves control even for
            # an org repo — and it doubles as the machine-readable de-list signal.
            delist = bool(info.get("delist"))
            src = lib.parse_github_repo(info.get("srcURL", "") or "")
            if not src:
                return None, None, False, delist, "srcURL is missing or not a github.com URL"
            owner, repo = src
            data, gherr = lib.gh_get_repo(owner, repo, token)
            # Only "gone" on a definitive 404 or explicit archived flag — NOT on a
            # transient error (rate limit / network), which would falsely verify.
            missing = data is None and bool(gherr) and "404" in gherr
            archived = bool(data and data.get("archived"))
            return owner, repo, (missing or archived), delist, None
    return None, None, False, False, f"'{repo_name}' is not in pluginList.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-list", default="pluginList.json")
    ap.add_argument("--output", default="delist-verify.md")
    args = ap.parse_args()

    author = (os.environ.get("ISSUE_AUTHOR") or "").strip()
    body = os.environ.get("ISSUE_BODY") or ""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo_name = field(body, "Plugin repoName") or field(body, "repoName")

    if not repo_name:
        verdict, msg = "error", "Could not read a **Plugin repoName** from the form."
    else:
        owner, repo, gone, delist, err = resolve_owner(repo_name, args.plugin_list, token)
        if err:
            verdict, msg = "error", f"Could not verify `{repo_name}`: {err}"
        elif delist:
            verdict, msg = "verified", (f"✅ Proof-of-control confirmed: `{owner}/{repo}`'s "
                                        f"`pluginInfo.json` declares `\"delist\": true` — only someone "
                                        f"with write access could set that.")
        elif gone:
            verdict, msg = "verified", (f"`{owner}/{repo}` is archived or no longer reachable — "
                                        f"de-listing is justified regardless of who asked.")
        elif author and owner and author.lower() == owner.lower():
            verdict, msg = "verified", (f"✅ Ownership confirmed: **@{author}** is the owner of "
                                        f"`{owner}/{repo}`.")
        else:
            verdict, msg = "unconfirmed", (
                f"⚠️ **@{author}** is not the direct owner of `{owner}/{repo}` "
                f"(it may be org-owned). GitHub won't confirm your write access to us, so please "
                f"**prove control**: set `\"delist\": true` in the plugin's `pluginInfo.json` and push "
                f"it (only someone with write access can). We'll detect it automatically. A maintainer "
                f"will review either way.")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"### De-list ownership check — `{verdict}`\n\n{msg}\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"verdict={verdict}\n")
    print(f"{verdict}: {msg}")


if __name__ == "__main__":
    main()
