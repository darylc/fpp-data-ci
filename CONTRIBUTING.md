# Contributing a plugin to FPP

This repository holds **`pluginList.json`**, the master index of community plugins shown in FPP's
Plugin Manager. This guide explains how to get your plugin listed and what our automated checks look
for.

There are two ways to submit:

1. **Issue Form (easiest — no git needed).** Open a
   [**Submit a plugin**](../../issues/new/choose) issue and fill in the form. A maintainer reviews it
   and adds your entry.
2. **Pull Request.** Add your entry to `pluginList.json` directly (details below). A CI check
   validates it automatically.

> ⚠️ Due to the increasing number of AI-generated plugins, please read the guidelines below
> carefully. Inclusion is at the FPP developers' discretion and is **not guaranteed**.

---

## Before you submit

1. **Announce your plugin** to the FPP community (e.g. the
   [FPP Facebook group](https://www.facebook.com/groups/1554782254796752)) and consider feedback.
2. **Test it** on the latest released FPP **and** the current nightly build.
3. **Follow FPP UI conventions** — at most **one** menu item per FPP menu (Status/Control, Content
   Setup, Input/Output Setup, Help); match the look and feel; keep the standard nav and status bar.
4. **Don't impact core FPP** — never interfere with stability or real-time sequence playback. Several
   supported boards have very limited CPU/RAM/flash; stay light even when idle.
5. **Only install where appropriate** — detect the platform and install/enable only what the hardware
   supports, or refuse to install cleanly with an explanation.
6. **Avoid subscription-locked features.**
7. **Include a license** in your repository.

---

## Your `pluginInfo.json`

Every plugin ships a `pluginInfo.json` at its repo root. It's the metadata FPP reads. Start from
[`fpp-plugin-Template`](https://github.com/FalconChristmas/fpp-plugin-Template). The full contract is
in [`.github/schema/pluginInfo.schema.json`](.github/schema/pluginInfo.schema.json). Required fields:

| Field | Meaning |
|---|---|
| `repoName` | Unique key — **must match your GitHub repo name and your pluginList.json entry name**. |
| `name` | Display name shown in the Plugin Manager. |
| `author` | Your name / handle. |
| `description` | One-line summary. |
| `homeURL` | Human-facing project page. |
| `srcURL` | The URL FPP **clones from** at install (the trust anchor — keep it accurate). |
| `bugURL` | Where users report bugs. **Enable Issues on that repo** or the link is dead. |
| `versions[]` | **Non-empty** list of supported FPP version ranges (see below). |

Each `versions[]` entry needs `minFPPVersion` and `branch`; `maxFPPVersion` (`"0"` = open-ended),
`sha` (a pinned commit — **recommended** for provenance), and `dependencies` are optional. Example:

```json
{
  "minFPPVersion": "10.0",
  "maxFPPVersion": "0",
  "branch": "main",
  "sha": "",
  "dependencies": { "packages": ["pulseaudio"] }
}
```

### Declaring dependencies (recommended)

FPP can install `apt` packages, script-repo scripts, and other plugins for you — and **ref-counts apt
packages** so uninstalling one plugin won't remove a package another still needs. Declare them in the
`dependencies` block instead of hand-installing in `fpp_install.sh` where possible.

**Older FPP compatibility:** FPP exports `FPP_DEPS_RESOLVED=1` before running your `fpp_install.sh` on
builds that understand the `dependencies` block. Guard any manual fallback install so it only runs on
older FPP:

```sh
[ -z "$FPP_DEPS_RESOLVED" ] && install_my_deps_manually   # old FPP only; new FPP already did it
```

---

## `pluginList.json` entry format

Add a single entry to the `pluginList` array, keeping the existing formatting. Two forms:

```json
[ "your-plugin-name", "https://raw.githubusercontent.com/<you>/<repo>/<branch>/pluginInfo.json" ]
[ "your-plugin-name", "https://raw.githubusercontent.com/<you>/<repo>/<branch>/pluginInfo.json", "Category" ]
```

The optional **3rd element is the category short name**. It must be one of the values in
[`pluginCategories.json`](pluginCategories.json) (the canonical list):

`Audio` · `Interaction` · `Payments` · `Messaging` · `Data Feeds` · `Home Automation` ·
`Notifications` · `Hardware` · `Display & Video` · `Monitoring`

Older FPP clients read only the first two elements, so the category is fully backward-compatible.

---

## What CI checks automatically

When you open a PR touching `pluginList.json`, the **Validate pluginList** check runs and posts a
comment. It only hard-checks **new or changed** entries, and reports:

**Errors (must fix — the check fails):**
- `pluginList.json` is valid JSON and every entry is a 2- or 3-element `[name, url, category?]` array.
- No duplicate `repoName`.
- Category (if given) is in `pluginCategories.json`.
- The `pluginInfo.json` URL loads and passes the schema; its `repoName` matches the list name.
- The `srcURL` repo exists and is public (unless `pluginInfo.json` declares `private: true`).

**Warnings (please review — the check still passes):**
- Issues are disabled on the `bugURL` repo (dead "Report a Bug" link).
- The repo is archived, or a URL couldn't be reached.

A green check means your entry is mechanically sound and ready for a **human review** (category fit, a
security glance at install scripts, and a quality sanity check). Reviewers may still ask for changes.

---

## After acceptance

Plugins from the `FalconChristmas` GitHub org show an **Official** badge; everyone else's install
behind a third-party confirmation. The list is periodically re-validated; entries whose repos
disappear or fall out of FPP-version compatibility may be flagged. The FPP developers reserve the
right to remove any plugin at any time.
