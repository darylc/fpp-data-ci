#!/usr/bin/env python3
"""Assert the hand-kept category copies still match pluginCategories.json.

WHY THIS EXISTS
---------------
pluginCategories.json is the single source of truth, but two places can't read it and
must keep a copy:

  1. .github/ISSUE_TEMPLATE/plugin-submission.yml — GitHub Issue Forms are static YAML
     and cannot fetch anything. Its dropdown is a hand-kept copy. This is the real risk:
     add a category upstream and the form silently offers a stale list forever.

  2. docs/contact/index.html — CATEGORIES_FALLBACK. The page fetches the real list at
     runtime, so this only shows if the fetch fails (offline/rate-limited). Lower stakes,
     but it should still not rot.

Nothing warns you when these drift — hence this check. Exits non-zero on mismatch.

Usage:
  check_category_drift.py --categories pluginCategories.json \
      --form .github/ISSUE_TEMPLATE/plugin-submission.yml \
      --page docs/contact/index.html
"""
import argparse
import json
import re
import sys

import yaml


def load_source_of_truth(path):
    data = json.load(open(path))
    arr = data if isinstance(data, list) else (data.get("categories") or data.get("pluginCategories") or [])
    names = [c["name"] if isinstance(c, dict) else c for c in arr]
    if not names:
        sys.exit(f"FATAL: no categories found in {path}")
    return names


def load_form_dropdown(path):
    doc = yaml.safe_load(open(path))
    for block in doc.get("body", []):
        if block.get("id") == "category" and block.get("type") == "dropdown":
            return list(block["attributes"]["options"])
    sys.exit(f"FATAL: no 'category' dropdown found in {path} — did the field id change?")


def load_page_fallback(path):
    """Pull CATEGORIES_FALLBACK out of the page. Entries are [short, long] pairs."""
    src = open(path).read()
    m = re.search(r"const\s+CATEGORIES_FALLBACK\s*=\s*\[(.*?)\];", src, re.DOTALL)
    if not m:
        sys.exit(f"FATAL: CATEGORIES_FALLBACK not found in {path} — was it renamed?")
    # Grab the first string of each [short, long] pair.
    return re.findall(r'\[\s*"([^"]+)"\s*,', m.group(1))


def report(label, truth, actual, errors):
    missing = [c for c in truth if c not in actual]     # in truth, not in the copy
    extra = [c for c in actual if c not in truth]       # in the copy, not in truth
    if not missing and not extra:
        print(f"  ✅ {label}: in sync ({len(actual)} categories)")
        return
    print(f"  ❌ {label}: OUT OF SYNC")
    for c in missing:
        print(f"       missing (add it): {c!r}")
        errors.append(f"{label} is missing category {c!r}")
    for c in extra:
        print(f"       unknown (remove or add to pluginCategories.json): {c!r}")
        errors.append(f"{label} has unknown category {c!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", required=True)
    ap.add_argument("--form")
    ap.add_argument("--page")
    args = ap.parse_args()

    truth = load_source_of_truth(args.categories)
    print(f"pluginCategories.json (source of truth): {len(truth)} categories")
    print(f"  {', '.join(truth)}\n")

    errors = []
    if args.form:
        report("Issue Form dropdown", truth, load_form_dropdown(args.form), errors)
    if args.page:
        report("Guided page fallback", truth, load_page_fallback(args.page), errors)

    if errors:
        print("\nCategory lists have drifted. pluginCategories.json is the source of truth —")
        print("update the copies to match it (or add the category there first).")
        return 1
    print("\nAll category copies match pluginCategories.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
