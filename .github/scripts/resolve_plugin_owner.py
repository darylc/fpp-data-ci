"""Print the registered owner (GitHub login) of a pluginList.json entry, from its
srcURL.

Used to authorize /recheck and /submit on a campaign tracking issue: unlike a
submission or removal issue (opened by the person who then acts on it), a tracking
issue is opened by the campaign itself - there's no "issue creator" to restrict
those commands to. The plugin's actual registered owner is the right authority
instead.

Usage: resolve_plugin_owner.py --plugin-list pluginList.json --repo-name <name>
Writes `owner` (empty if unresolvable) to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_plugin_schema import fetch_json, load_pluginlist, parse_github_repo  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-list", required=True)
    ap.add_argument("--repo-name", required=True)
    args = ap.parse_args()

    owner = ""
    entry = next((e for e in load_pluginlist(args.plugin_list)
                  if e and e[0].lower() == args.repo_name.lower()), None)
    if entry:
        info_url = entry[1] if len(entry) > 1 else None
        info, _ = fetch_json(info_url) if info_url else (None, None)
        src = parse_github_repo((info or {}).get("srcURL", "") or "")
        if src:
            owner = src[0]

    print(f"owner={owner or '(unresolved)'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"owner={owner}\n")


if __name__ == "__main__":
    main()
