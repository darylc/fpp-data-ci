"""Static compliance linter for a single FPP plugin working tree.

Runs the guideline/hygiene checks (the "areas of concern / optimisation"
surfaced in a release-readiness scan) against a plugin's cloned directory.
Pure standard library, no clone/network here — the caller provides a path.

Each check yields a Finding(severity, code, message). Severities:
  blocker        — dangerous or breaks FPP/other users (reboots the box, kills a running
                   show, remote code exec, world-writable, corrupts the system Python,
                   bypasses the stable API contract)
  best-practice  — against the guidelines but not dangerous (sudo in a script, no
                   `set -e`, no uninstall script, CRLF line endings)
  optional       — polish (missing LICENSE/README, no bugURL)

Reference: PLUGIN_GUIDELINES.md and PLUGININFO_FORMAT.md in fpp-plugin-Template.

Standalone:  python lint_plugin.py <plugin_dir> [repoName]
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

BLOCKER, BEST_PRACTICE, OPTIONAL = "blocker", "best-practice", "optional"

HOOKS = ("fpp_install.sh", "fpp_uninstall.sh", "preStart.sh", "postStart.sh",
         "preStop.sh", "postStop.sh")
SCRIPT_EXT = (".sh", ".py", ".php")


@dataclass
class Finding:
    severity: str
    code: str
    message: str


def _iter_files(root: str, exts=None):
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            if exts and not fn.endswith(exts):
                continue
            yield os.path.join(dirpath, fn)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _grep(root, pattern, exts=SCRIPT_EXT, flags=re.I):
    """Yield (relpath, lineno, line) for a regex over code files, skipping docs."""
    rx = re.compile(pattern, flags)
    for path in _iter_files(root, exts):
        rel = os.path.relpath(path, root)
        low = rel.lower()
        if low.endswith((".md", ".markdown")) or "/help/" in "/" + low or "/test" in "/" + low:
            continue
        for i, line in enumerate(_read(path).splitlines(), 1):
            stripped = line.lstrip()
            # skip full comment lines (PHP/JS/C //*, shell/py #, HTML <!--, ini ;)
            if stripped[:2] in ("//", "/*", "* ") or stripped[:1] in ("#", ";") \
               or stripped.startswith("<!--") or stripped in ("*", "*/"):
                continue
            if rx.search(line):
                yield rel, i, line.strip()


def lint_plugin_dir(root: str, repo_name: str | None = None) -> list[Finding]:
    """Run all static checks against a plugin working tree; return findings."""
    out: list[Finding] = []
    repo = repo_name or os.path.basename(os.path.normpath(root))
    names = os.listdir(root) if os.path.isdir(root) else []
    lower = {n.lower() for n in names}

    def first(pattern, exts=SCRIPT_EXT):
        for hit in _grep(root, pattern, exts):
            return hit
        return None

    # --- dangerous host behaviour -------------------------------------------
    hit = first(r'(curl|wget)\b[^|]*\|[^|]*(sudo\s+)?(bash|sh)\b')
    if hit:
        out.append(Finding(BLOCKER, "remote-exec",
                   f"pipes a remote script into a shell ({hit[0]}:{hit[1]}: `{hit[2]}`) — declare "
                   f"a dependency, or download to a file, verify its checksum, then run it, e.g. "
                   f"`curl -fsSLo installer.sh https://example.com/install.sh && "
                   f"echo \"<sha256>  installer.sh\" | sha256sum -c && bash installer.sh`"))

    # Reboots/shutdowns are an error. A bare reboot/shutdown only counts as a
    # command (start of line / after ;&| / sudo / then|do, in a shell script, or
    # wrapped in system()/exec()) — not the word "Reboot" in UI text.
    hit = (next(iter(_grep(root, r'(^|[;&|]|\bsudo\s+|\bthen\s+|\bdo\s+)\s*(reboot|shutdown|halt)\b',
                           exts=(".sh",))), None)
           or first(r'(system|exec|shell_exec|passthru|popen)\s*\([^)]*\b(reboot|shutdown)\b'))
    if hit:
        out.append(Finding(BLOCKER, "reboot",
                   f"reboots/shuts down the box ({hit[0]}:{hit[1]}: `{hit[2]}`) — replace it with "
                   f"`setSetting rebootFlag 1` (shell) or the equivalent in your language, so FPP "
                   f"reboots on its own schedule instead of pulling the box down mid-show"))

    # Restarting fppd DIRECTLY (RestartFPPD(), systemctl/service/kill, `fpp -r`) is
    # the anti-pattern. The sanctioned way is SetRestartFlag()/`setSetting restartFlag`
    # (deferred, sequenced around a running show) — those are NOT flagged.
    hit = first(r'\bRestartFPPD\s*\(|\bfppd_restart\b|systemctl\s+(restart|stop|start)\s+fppd'
                r'|service\s+fppd\s+(restart|stop)|(pkill|killall)\s+[^\n]*fppd|\bfpp\s+-r\b|\bfpp\s+--restart\b'
                r'|/api/system/fppd/(restart|reboot)|api/system/restart')
    if hit:
        out.append(Finding(BLOCKER, "fppd-restart",
                   f"restarts fppd directly ({hit[0]}:{hit[1]}: `{hit[2]}`) — replace it with "
                   f"`setSetting restartFlag 1` (shell) or `SetRestartFlag()` (C++), so FPP "
                   f"restarts safely between sequences instead of killing a running show"))

    # Hitting fppd's raw port 32322 bypasses the documented, Apache-proxied API.
    # Match only real URLs (http://host:32322…), not comments like "…proxies to
    # localhost:32322/LoRa" that describe the plugin-apis registration mechanism.
    hit = first(r'https?://(localhost|127\.0\.0\.1|0\.0\.0\.0):32322')
    if hit:
        out.append(Finding(BLOCKER, "fppd-port",
                   f"calls fppd's internal port :32322 directly ({hit[0]}:{hit[1]}: `{hit[2]}`) — "
                   f"replace `http://localhost:32322/...` with the proxied, documented equivalent "
                   f"at `http://localhost/api/...` instead"))

    hit = first(r'pip3?\s+install[^\n]*--break-system-packages')
    if hit:
        out.append(Finding(BLOCKER, "break-system-packages",
                   f"pip --break-system-packages corrupts the system Python ({hit[0]}:{hit[1]}: "
                   f"`{hit[2]}`) — create a venv inside your plugin directory instead, e.g. "
                   f"`python3 -m venv \"$SCRIPT_DIR/venv\" && \"$SCRIPT_DIR/venv/bin/pip\" install ...`, "
                   f"then call `\"$SCRIPT_DIR/venv/bin/python3\"` from your plugin code"))

    # Reading/parsing FPP's raw core config directly (the settings file, channel
    # outputs) is fragile — use getSetting()/$settings/the API. Writing your OWN
    # config via WriteSettingToFile(key, val, pluginName) is fine and NOT flagged.
    hit = first(r'''(open|file_get_contents|fopen|fgets|cat)\s*\(?\s*['"]?[^'"\n]*media/settings\b'''
                r'''|['"][^'"\n]*/(channeloutputs|co-universes|co-pixelStrings)\.json''')
    if hit:
        out.append(Finding(BLOCKER, "core-config",
                   f"reads/writes FPP core config directly ({hit[0]}:{hit[1]}: `{hit[2]}`) — read "
                   f"it through `getSetting('settingName')` (PHP) or the `/api/settings/<name>` "
                   f"endpoint instead of parsing the settings file yourself; the file's format is "
                   f"not a stable contract across FPP releases"))

    hit = first(r'chmod\s+(-R\s+)?(777|666|a\+w|o\+w)\b')
    if hit:
        out.append(Finding(BLOCKER, "world-writable",
                   f"loosens permissions to world-writable ({hit[0]}:{hit[1]}: `{hit[2]}`) — since "
                   f"install/hooks already run as root, scope the permission to just the owner or "
                   f"group that needs it (e.g. `chmod 750` for a directory another service-user "
                   f"reads, or `chown` that user instead of opening it to everyone)"))

    hit = first(r'\bsudo\b', exts=(".sh",))
    if hit:
        out.append(Finding(BEST_PRACTICE, "sudo",
                   f"uses sudo in a script ({hit[0]}:{hit[1]}: `{hit[2]}`) — install/hooks already "
                   f"run as root, so remove the sudo call and run the command directly, e.g. "
                   f"`{hit[2].replace('sudo ', '', 1)}`"))

    # --- shell script hygiene ------------------------------------------------
    for path in _iter_files(root, (".sh",)):
        rel = os.path.relpath(path, root)
        head = _read(path).splitlines()
        if not head or not head[0].startswith("#!"):
            out.append(Finding(BEST_PRACTICE, "no-shebang",
                       f"{rel} has no shebang line — add `#!/bin/bash` (or `#!/bin/sh`) as its "
                       f"first line so it runs with a known shell regardless of how it's invoked"))
        try:
            with open(path, "rb") as f:
                raw_lines = f.read().split(b"\n")
        except OSError:
            raw_lines = []
        lines_with_cr = [i for i, line in enumerate(raw_lines, 1) if line.endswith(b"\r")]
        if lines_with_cr:
            out.append(Finding(BEST_PRACTICE, "crlf",
                       f"{rel}:{lines_with_cr[0]} has CRLF line endings — breaks bash (the `\\r` "
                       f"becomes part of the command). Fix with `sed -i 's/\\r$//' {rel}` or "
                       f"`dos2unix {rel}`, and configure your editor/git to use LF"))

    # hook exec bits (FPP execs preStart/... directly; non-+x hooks silently don't run)
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            if fn in HOOKS:
                p = os.path.join(dirpath, fn)
                if not os.access(p, os.X_OK):
                    sev = BLOCKER if fn.startswith(("preStart", "postStart", "preStop", "postStop")) else BEST_PRACTICE
                    out.append(Finding(sev, "exec-bit",
                               f"{os.path.relpath(p, root)} is not executable — commit it +x "
                               f"(git update-index --chmod=+x)"))

    # install error handling
    for cand in ("scripts/fpp_install.sh", "fpp_install.sh"):
        p = os.path.join(root, cand)
        if os.path.isfile(p):
            body = _read(p)
            if not re.search(r'set\s+-e|set\s+-euo|\|\|\s*exit', body):
                out.append(Finding(BEST_PRACTICE, "no-set-e",
                           f"{cand} has no 'set -e' (or `|| exit`) — without it, bash keeps running "
                           f"the rest of the script even after a command fails, so if an earlier "
                           f"step errors out (e.g. a dependency install fails), later steps still "
                           f"run against that broken state and the plugin ends up half-installed "
                           f"with no visible error. Add `set -e` (or `set -euo pipefail`) as the "
                           f"first line after the shebang so the script stops immediately on the "
                           f"first failure instead"))
            break

    # --- logging conventions -------------------------------------------------
    log_hit = first(r'''(['"][^'"]*\.log['"])|>>?\s*\S*\.log''')
    if log_hit:
        # crude: flag logs written to plugin dir (script_dir) or /tmp
        bad_hit = first(r'script_dir\s*\+\s*[^\n]*\.log') or first(r'/tmp/\S*\.log')
        if bad_hit:
            out.append(Finding(BEST_PRACTICE, "log-location",
                       f"writes a log outside FPP's logs directory ({bad_hit[0]}:{bad_hit[1]}: "
                       f"`{bad_hit[2]}`) — use <logdir>/plugin-{repo}.log so it is rotated and in "
                       f"the Support Zip"))

    # --- repo hygiene --------------------------------------------------------
    if not any(n.startswith(("license", "copying")) for n in lower):
        out.append(Finding(OPTIONAL, "no-license", "no LICENSE file — add one for redistribution clarity"))
    if not any(n.startswith("readme") for n in lower):
        out.append(Finding(OPTIONAL, "no-readme", "no README file"))

    # installs a systemd unit but ships no uninstall script
    if first(r'/etc/systemd/system/|systemctl\s+enable') and \
       not (os.path.isfile(os.path.join(root, "scripts/fpp_uninstall.sh")) or
            os.path.isfile(os.path.join(root, "fpp_uninstall.sh"))):
        out.append(Finding(BLOCKER, "no-uninstall",
                   "creates a systemd service but ships no fpp_uninstall.sh to remove it — add "
                   "one that mirrors the install, e.g. `systemctl disable --now <unit> && rm -f "
                   "/etc/systemd/system/<unit>`, so removing the plugin doesn't leave an orphaned "
                   "service behind"))

    return out


def main(argv):
    if len(argv) < 2:
        print("usage: lint_plugin.py <plugin_dir> [repoName]", file=sys.stderr)
        return 2
    findings = lint_plugin_dir(argv[1], argv[2] if len(argv) > 2 else None)
    for f in findings:
        print(f"{f.severity.upper():5} [{f.code}] {f.message}")
    print(f"\n{len(findings)} finding(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
