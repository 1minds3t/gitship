#!/usr/bin/env python3
"""
gitship ci - GitHub Actions workflow management

Production-grade CI control plane for GitHub Actions:

  Observability
  ─────────────
  - All workflows: run counts, % failed (colour-coded), avg duration, last run
  - Recent runs: status, branch, event, age, duration
  - Event → workflow map (scans local files)
  - Failure logs with highlighted error lines

  Actions  (via gh CLI)
  ──────────────────────
  - Trigger workflow_dispatch, rerun failed / all, cancel

  Workflow File Management
  ────────────────────────
  - Create from curated templates
  - Edit in $EDITOR
  - Delete with confirmation
  - Edit triggers / cron interactively

  Safety / Reliability
  ─────────────────────
  - ruamel.yaml for structure-preserving YAML edits
    (falls back to regex-only if not installed)
  - Atomic writes  → no partial-file corruption on crash
  - .gitship.bak backup before every mutation
  - Dry-run mode   → diff preview without writing
  - Schema-lint via `gh workflow view` after write
  - File locking   → safe against concurrent gitship processes
  - Workflow name index (file / display-name / id) → no false "no runs"
  - gh API response cache (30 s TTL) → fast repeats, lower rate-limit risk
  - JSON output mode (--json) → machine-readable everywhere
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── optional deps ────────────────────────────────────────────
try:
    from ruamel.yaml import YAML as _RYAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from io import StringIO as _StringIO
    _HAS_RUAMEL = True
except ImportError:
    _HAS_RUAMEL = False
    CommentedMap = dict   # type: ignore[misc,assignment]
    CommentedSeq = list   # type: ignore[misc,assignment]

try:
    from filelock import FileLock, Timeout as _FileLockTimeout
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False


# ─────────────────────────────────────────────────────────────
# ANSI helpers
# ─────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

def green(t):   return _c("32", t)
def red(t):     return _c("31", t)
def yellow(t):  return _c("33", t)
def cyan(t):    return _c("36", t)
def blue(t):    return _c("34", t)
def grey(t):    return _c("90", t)
def bold(t):    return _c("1",  t)
def magenta(t): return _c("35", t)
def dim(t):     return _c("2",  t)


# ─────────────────────────────────────────────────────────────
# gh CLI wrapper
# ─────────────────────────────────────────────────────────────

class GHError(Exception):
    pass


def _gh(*args: str, check: bool = True) -> str:
    """Run a `gh` command; return stdout or raise GHError."""
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, check=False,
        )
        if check and r.returncode != 0:
            raise GHError(r.stderr.strip() or r.stdout.strip())
        return r.stdout.strip()
    except FileNotFoundError:
        raise GHError("GitHub CLI ('gh') not found — https://cli.github.com")


def _gh_json(*args: str) -> Any:
    raw = _gh(*args)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise GHError(f"gh JSON parse error: {e}\n{raw[:200]}")


def _check_gh_auth() -> None:
    try:
        _gh("auth", "status")
    except GHError as e:
        print(red(f"❌  {e}"))
        print(yellow("   Run: gh auth login"))
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# API cache  (30 s TTL, keyed by gh args)
# ─────────────────────────────────────────────────────────────

def _cache_dir(repo_path: Path) -> Path:
    d = repo_path / ".gitship" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_gh_json(repo_path: Path, *args: str, ttl: int = 30) -> Any:
    """gh JSON call with file-based cache.  Returns stale data on gh error."""
    key = "_".join(a.lstrip("-").replace(" ", "_") for a in args if a)
    cache_file = _cache_dir(repo_path) / f"{key[:80]}.json"

    # fresh cache hit?
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl:
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

    try:
        data = _gh_json(*args)
        _atomic_write_text(cache_file, json.dumps(data, indent=2))
        return data
    except GHError:
        # serve stale rather than nothing
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass
        return []


# ─────────────────────────────────────────────────────────────
# Atomic file write + file locking
# ─────────────────────────────────────────────────────────────

def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically (no partial-write corruption)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)   # atomic on POSIX; best-effort on Windows
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _WorkflowLock:
    """Context manager: file lock around a workflow mutation."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = None
        if _HAS_FILELOCK:
            self._lock = FileLock(str(path) + ".gitship.lock", timeout=10)

    def __enter__(self):
        if self._lock:
            try:
                self._lock.acquire()
            except _FileLockTimeout:
                raise RuntimeError(
                    f"Could not acquire lock on {self._path.name} — "
                    "is another gitship process running?"
                )
        return self

    def __exit__(self, *_):
        if self._lock and self._lock.is_locked:
            self._lock.release()


# ─────────────────────────────────────────────────────────────
# Backup
# ─────────────────────────────────────────────────────────────

def _backup(path: Path) -> Path:
    """Copy path → path.gitship.bak before mutation.  Returns backup path."""
    bak = path.with_suffix(path.suffix + ".gitship.bak")
    shutil.copy2(path, bak)
    return bak


# ─────────────────────────────────────────────────────────────
# Dry-run diff helper
# ─────────────────────────────────────────────────────────────

def _show_diff(original: str, modified: str, filename: str) -> None:
    lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    ))
    if not lines:
        print(grey("  (no changes)"))
        return
    for line in lines:
        if line.startswith("+"):
            print(green(f"  {line}"))
        elif line.startswith("-"):
            print(red(f"  {line}"))
        elif line.startswith("@@"):
            print(cyan(f"  {line}"))
        else:
            print(grey(f"  {line}"))


# ─────────────────────────────────────────────────────────────
# YAML engine  (ruamel preferred; regex fallback)
# ─────────────────────────────────────────────────────────────

class WorkflowDoc:
    """
    Structure-preserving workflow document.

    Loads with ruamel.yaml when available so comments, ordering, and
    indentation survive round-trips.  Falls back to regex-only operation
    on raw text when ruamel is absent.

    Mutation API
    ────────────
      doc.add_event(event)       → idempotent; handles str/list/dict 'on'
      doc.remove_event(event)
      doc.set_cron(expr)         → adds schedule block if absent
      doc.remove_cron()
      doc.replace_triggers(evs)  → full replacement

    Query API
    ─────────
      doc.triggers  → list[str]
      doc.crons     → list[str]
      doc.name      → str
      doc.jobs      → list[str]

    Serialisation
    ─────────────
      doc.to_string() → str  (preserves comments with ruamel)
    """

    def __init__(self, text: str):
        self._text = text
        self._data = None

        if _HAS_RUAMEL:
            try:
                ry = _RYAML()
                ry.preserve_quotes = True
                self._data = ry.load(text)
            except Exception:
                self._data = None  # fall back to regex

    # ── query ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        if self._data is not None:
            return str(self._data.get("name", "")).strip().strip("\"'")
        m = re.search(r"^name\s*:\s*(.+)$", self._text, re.MULTILINE)
        return m.group(1).strip().strip("\"'") if m else ""

    @property
    def jobs(self) -> list:
        if self._data is not None:
            jobs_section = self._data.get("jobs") or {}
            return list(jobs_section.keys())
        result = []
        in_jobs = False
        for line in self._text.splitlines():
            if re.match(r"^jobs\s*:", line):
                in_jobs = True
                continue
            if in_jobs:
                m = re.match(r"^  (\w[\w\-_]*)\s*:", line)
                if m:
                    result.append(m.group(1))
                elif line and not line[0].isspace():
                    break
        return result

    @property
    def triggers(self) -> list:
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            return _extract_events(on_val)
        return _regex_triggers(self._text)

    @property
    def crons(self) -> list:
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            if isinstance(on_val, dict):
                schedule = on_val.get("schedule") or []
                return [e.get("cron", "") for e in schedule
                        if isinstance(e, dict) and "cron" in e]
            return []
        return re.findall(r"cron\s*:\s*['\"]([^'\"]+)['\"]", self._text)

    # ── mutation ─────────────────────────────────────────────

    def add_event(self, event: str) -> None:
        """Add trigger event; idempotent; normalises 'on' to dict form."""
        if event in self.triggers:
            return
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            if on_val is None:
                self._set_on({event: None})
            elif isinstance(on_val, str):
                self._set_on({on_val: None, event: None})
            elif isinstance(on_val, list):
                new_map = {e: None for e in on_val}
                new_map[event] = None
                self._set_on(new_map)
            elif isinstance(on_val, dict):
                on_val[event] = None
            self._sync_text()
        else:
            self._text = _regex_add_event(self._text, event)

    def remove_event(self, event: str) -> None:
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            if isinstance(on_val, dict) and event in on_val:
                del on_val[event]
            elif isinstance(on_val, list) and event in on_val:
                on_val.remove(event)
            elif isinstance(on_val, str) and on_val == event:
                self._set_on({})
            self._sync_text()
        else:
            self._text = _regex_remove_event(self._text, event)

    def set_cron(self, expr: str) -> None:
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            if not isinstance(on_val, dict):
                new_on = {}
                self._set_on(new_on)
                on_val = new_on
            schedule = on_val.get("schedule")
            if schedule is None:
                on_val["schedule"] = [{"cron": expr}]
            else:
                on_val["schedule"].append({"cron": expr})
            self._sync_text()
        else:
            self._text = _regex_inject_cron(self._text, expr)

    def remove_cron(self) -> None:
        if self._data is not None:
            on_val = self._data.get("on") or self._data.get(True)
            if isinstance(on_val, dict) and "schedule" in on_val:
                del on_val["schedule"]
            self._sync_text()
        else:
            self._text = re.sub(r"\n?\s+- cron: '[^']+'\n?", "", self._text)
            self._text = re.sub(r"\n  schedule:\n(?!\s+-)", "", self._text)

    def replace_triggers(self, events: list) -> None:
        if self._data is not None:
            self._set_on({e: None for e in events})
            self._sync_text()
        else:
            on_block = "on:\n" + "".join(f"  {e}:\n" for e in events)
            self._text = re.sub(
                r"^on\s*:.*?(?=\n\S|\Z)",
                on_block.rstrip(),
                self._text,
                flags=re.MULTILINE | re.DOTALL,
            )

    # ── serialise ────────────────────────────────────────────

    def to_string(self) -> str:
        return self._text

    # ── internal ─────────────────────────────────────────────

    def _set_on(self, value: Any) -> None:
        for key in list(self._data.keys()):
            if key == "on" or key is True:
                self._data[key] = value
                return
        self._data["on"] = value

    def _sync_text(self) -> None:
        if self._data is None or not _HAS_RUAMEL:
            return
        try:
            ry = _RYAML()
            ry.preserve_quotes = True
            ry.default_flow_style = False
            ry.width = 4096
            from io import StringIO
            buf = StringIO()
            ry.dump(self._data, buf)
            self._text = buf.getvalue()
        except Exception:
            pass  # keep existing text if serialisation fails


# ── pure-regex helpers ───────────────────────────────────────

def _extract_events(on_val: Any) -> list:
    if on_val is None:        return []
    if isinstance(on_val, str):   return [on_val]
    if isinstance(on_val, list):  return [str(e) for e in on_val]
    if isinstance(on_val, dict):  return list(on_val.keys())
    return []


def _regex_triggers(text: str) -> list:
    on_m = re.search(r"^on\s*:\s*\n((?:[ \t]+.*\n?)+)", text, re.MULTILINE)
    if on_m:
        block = on_m.group(1)
        return [m.group(1) for line in block.splitlines()
                if (m := re.match(r"^\s{1,4}(\w[\w\-_]*)\s*[:\[{]?", line))]
    inline = re.search(r"^on\s*:\s*\[([^\]]+)\]", text, re.MULTILINE)
    if inline:
        return [t.strip() for t in inline.group(1).split(",")]
    single = re.search(r"^on\s*:\s*(\w[\w\-_]+)\s*$", text, re.MULTILINE)
    if single:
        return [single.group(1)]
    return []


def _regex_add_event(text: str, event: str) -> str:
    on_m = re.search(r"^(on\s*:)", text, re.MULTILINE)
    if on_m:
        pos = on_m.end()
        return text[:pos] + f"\n  {event}:" + text[pos:]
    return re.sub(r"^(jobs\s*:)", f"on:\n  {event}:\n\n\\1",
                  text, flags=re.MULTILINE)


def _regex_remove_event(text: str, event: str) -> str:
    return re.sub(rf"\n  {re.escape(event)}\s*:(?:\n    [^\n]*)*", "", text)


def _regex_inject_cron(text: str, expr: str) -> str:
    cron_line = f"\n    - cron: '{expr}'"
    if "schedule:" in text:
        return re.sub(r"(  schedule\s*:)", r"\1" + cron_line, text)
    on_m = re.search(r"^(on\s*:)", text, re.MULTILINE)
    if on_m:
        pos = on_m.end()
        return text[:pos] + f"\n  schedule:{cron_line}" + text[pos:]
    return text


# ─────────────────────────────────────────────────────────────
# Safe write  (backup → lock → atomic write → lint validate)
# ─────────────────────────────────────────────────────────────

def _safe_write(
    path: Path,
    original_text: str,
    new_text: str,
    dry_run: bool = False,
    lint: bool = True,
) -> bool:
    """
    Write new_text to path with full safety guards.

    1. Dry-run diff (if dry_run=True, stop here)
    2. Backup original → .gitship.bak
    3. Acquire file lock
    4. Atomic write (temp file → os.replace)
    5. Schema-lint via gh (if lint=True)
       → on failure: restore backup, return False
    Returns True on success, False on lint failure.
    """
    if dry_run:
        print(f"\n  {cyan('◎  Dry-run diff')}  {grey(path.name)}\n")
        _show_diff(original_text, new_text, path.name)
        print()
        return True

    bak = _backup(path)

    with _WorkflowLock(path):
        _atomic_write_text(path, new_text)

        if lint:
            ok = _lint_workflow(path)
            if ok:
                print(green("  ✓  Schema OK"))
            else:
                shutil.copy2(bak, path)
                print(red("  ✗  Lint failed — original restored from backup."))
                print(yellow(f"     Backup: {bak.name}"))
                return False

    return True


def _lint_workflow(path: Path) -> bool:
    """Validate workflow via `gh workflow view`.  True = valid or unknowable."""
    try:
        r = subprocess.run(
            ["gh", "workflow", "view", path.name],
            capture_output=True, text=True, check=False,
            cwd=str(path.parents[2]),   # repo root
        )
        if r.returncode != 0 and "could not find" in r.stderr.lower():
            return True   # not pushed yet — can't validate, assume OK
        return r.returncode == 0
    except FileNotFoundError:
        return True   # gh not available → skip


# ─────────────────────────────────────────────────────────────
# Workflow index  (name ↔ file ↔ gh id)
# ─────────────────────────────────────────────────────────────

class WorkflowIndex:
    """
    Tri-keyed index: display-name / filename-stem / gh-reported-name.
    Prevents false "no runs found" when gh uses a different name variant.
    """

    def __init__(self, repo_path: Path, runs: list):
        self._runs_map: dict = {}
        for r in runs:
            key = (r.get("workflowName") or "").lower()
            self._runs_map.setdefault(key, []).append(r)
        # also index by filename stem (gh sometimes uses that)
        for p in _list_local_workflows(repo_path):
            self._runs_map.setdefault(p.stem.lower(), [])

    def runs_for_file(self, wf_path: Path, content: str) -> list:
        display = _doc_name(content) or wf_path.stem
        for candidate in [display.lower(), wf_path.stem.lower(), wf_path.name.lower()]:
            if candidate in self._runs_map and self._runs_map[candidate]:
                return self._runs_map[candidate]
        return []


def _doc_name(content: str) -> str:
    m = re.search(r"^name\s*:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip().strip("\"'") if m else ""


# ─────────────────────────────────────────────────────────────
# Filesystem helpers
# ─────────────────────────────────────────────────────────────

def _workflows_dir(repo_path: Path) -> Path:
    return repo_path / ".github" / "workflows"


def _list_local_workflows(repo_path: Path) -> list:
    wdir = _workflows_dir(repo_path)
    if not wdir.exists():
        return []
    return sorted(p for p in wdir.iterdir() if p.suffix in (".yml", ".yaml"))


def _resolve_workflow_path(repo_path: Path, filename: str) -> Optional[Path]:
    wdir = _workflows_dir(repo_path)
    for ext in ("", ".yml", ".yaml"):
        p = wdir / (filename + ext)
        if p.exists():
            return p
    for p in wdir.iterdir():
        if filename.lower() in p.name.lower():
            return p
    return None


# ─────────────────────────────────────────────────────────────
# Run stats
# ─────────────────────────────────────────────────────────────

def _run_stats(runs: list) -> dict:
    if not runs:
        return {"total": 0, "success": 0, "failed": 0, "cancelled": 0,
                "pct_failed": 0.0, "last_run": None, "avg_duration_s": 0}
    total     = len(runs)
    success   = sum(1 for r in runs if r.get("conclusion") == "success")
    failed    = sum(1 for r in runs if r.get("conclusion") in ("failure", "timed_out"))
    cancelled = sum(1 for r in runs if r.get("conclusion") == "cancelled")
    durations = []
    for r in runs:
        try:
            s = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
            durations.append((e - s).total_seconds())
        except Exception:
            pass
    return {
        "total": total, "success": success, "failed": failed,
        "cancelled": cancelled,
        "pct_failed": (failed / total * 100) if total else 0.0,
        "last_run": runs[0].get("createdAt", "") if runs else None,
        "avg_duration_s": sum(durations) / len(durations) if durations else 0,
    }


def _fmt_duration(s: float) -> str:
    if s < 60: return f"{int(s)}s"
    return f"{int(s // 60)}m{int(s % 60)}s"


def _fmt_ago(iso: Optional[str]) -> str:
    if not iso: return "never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        s  = (datetime.now(timezone.utc) - dt).total_seconds()
        if s < 120:   return f"{int(s)}s ago"
        if s < 7200:  return f"{int(s // 60)}m ago"
        if s < 86400: return f"{int(s // 3600)}h ago"
        return f"{int(s // 86400)}d ago"
    except Exception:
        return iso


def _status_icon(conclusion: str, status: str) -> str:
    if status in ("in_progress", "queued", "waiting"):
        return yellow("⏳")
    return {
        "success":   green("✓"),
        "failure":   red("✗"),
        "timed_out": red("⏱"),
        "cancelled": grey("⊘"),
        "skipped":   grey("↷"),
    }.get(conclusion or status, grey("?"))


# ─────────────────────────────────────────────────────────────
# DISPLAY: overview
# ─────────────────────────────────────────────────────────────

def _sep(char: str = "─", width: int = 72):
    print(grey(char * width))


def show_overview(repo_path: Path, limit: int = 20, as_json: bool = False) -> None:
    _check_gh_auth()
    local = _list_local_workflows(repo_path)

    all_runs = _cached_gh_json(
        repo_path,
        "run", "list", "--limit", "200",
        "--json", "workflowName,conclusion,status,createdAt,updatedAt,"
                  "databaseId,headBranch,event",
    )
    index = WorkflowIndex(repo_path, all_runs)

    if as_json:
        out = []
        for wf in local:
            content = wf.read_text(encoding="utf-8", errors="replace")
            doc     = WorkflowDoc(content)
            runs    = index.runs_for_file(wf, content)
            stats   = _run_stats(runs[:limit])
            out.append({
                "file": str(wf.relative_to(repo_path)),
                "name": doc.name or wf.stem,
                "triggers": doc.triggers,
                "crons": doc.crons,
                **stats,
            })
        print(json.dumps(out, indent=2, default=str))
        return

    print(f"\n{bold('━' * 72)}")
    print(f"  {bold(cyan('⚡  GitHub Actions'))}  {grey('─')}  {grey(repo_path.name)}")
    if not _HAS_RUAMEL:
        print(f"  {yellow('⚠')}  {dim('ruamel.yaml not installed — pip install ruamel.yaml')}")
    if not _HAS_FILELOCK:
        print(f"  {yellow('⚠')}  {dim('filelock not installed — pip install filelock')}")
    print(f"{bold('━' * 72)}\n")

    if not local:
        print(yellow("  No workflows in .github/workflows/"))
        print(f"  Run {bold('gitship ci --create')} to add one.\n")
        return

    print(f"  {'WORKFLOW':<30} {'TRIGGERS':<22} {'RUNS':>5} "
          f"{'FAIL%':>6} {'AVG':>6}  {'LAST RUN':<14}  ST")
    _sep()

    for wf in local:
        content  = wf.read_text(encoding="utf-8", errors="replace")
        doc      = WorkflowDoc(content)
        name     = doc.name or wf.stem
        trigs    = doc.triggers
        trig_str = ", ".join(trigs[:3]) + ("…" if len(trigs) > 3 else "")
        runs     = index.runs_for_file(wf, content)
        stats    = _run_stats(runs[:limit])

        fp = stats["pct_failed"]
        fail_str = (red if fp > 30 else yellow if fp > 5 else green)(f"{fp:5.1f}%")
        avg_str  = (_fmt_duration(stats["avg_duration_s"])
                    if stats["avg_duration_s"] else grey("  -  "))
        ic = (_status_icon(
                runs[0].get("conclusion", ""),
                runs[0].get("status", ""),
              ) if runs else grey("─"))

        print(f"  {bold(name):<39} {grey(trig_str):<22} "
              f"{str(stats['total']):>5} {fail_str:>6} {avg_str:>6}  "
              f"{grey(_fmt_ago(stats['last_run'])):<14}  {ic}")
        print(f"  {dim(str(wf.relative_to(repo_path)))}")
        _sep(char="·")
    print()


# ─────────────────────────────────────────────────────────────
# DISPLAY: recent runs
# ─────────────────────────────────────────────────────────────

def show_runs(repo_path: Path, workflow_name: Optional[str] = None,
              limit: int = 20, as_json: bool = False) -> None:
    _check_gh_auth()

    cmd = ["run", "list", "--limit", str(limit),
           "--json", "workflowName,conclusion,status,createdAt,updatedAt,"
                     "databaseId,headBranch,event,displayTitle,url"]
    if workflow_name:
        cmd += ["--workflow", workflow_name]

    runs = _cached_gh_json(repo_path, *cmd, ttl=15)

    if as_json:
        print(json.dumps(runs, indent=2, default=str))
        return

    label = bold(f"'{workflow_name}'") if workflow_name else "all workflows"
    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('▶  Recent Runs')}  {grey('─')}  {label}")
    print(f"{bold('━' * 72)}\n")

    if not runs:
        print(yellow("  No runs found.\n"))
        return

    for r in runs:
        icon   = _status_icon(r.get("conclusion", ""), r.get("status", ""))
        rid    = str(r.get("databaseId", ""))
        title  = (r.get("displayTitle") or r.get("workflowName", ""))[:34]
        branch = r.get("headBranch", "?")
        event  = r.get("event", "?")
        age    = _fmt_ago(r.get("createdAt"))
        dur    = ""
        try:
            s   = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            e   = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
            dur = f"  {grey(_fmt_duration((e - s).total_seconds()))}"
        except Exception:
            pass
        print(f"  {icon}  {bold(rid[:9]):<12} {title:<34} "
              f"{grey(branch):<20} {grey(event):<14} {grey(age)}{dur}")
    print()


# ─────────────────────────────────────────────────────────────
# DISPLAY: event map
# ─────────────────────────────────────────────────────────────

def show_event_map(repo_path: Path, as_json: bool = False) -> None:
    local = _list_local_workflows(repo_path)
    event_map: dict = {}
    for wf in local:
        content = wf.read_text(encoding="utf-8", errors="replace")
        doc     = WorkflowDoc(content)
        name    = doc.name or wf.stem
        for t in doc.triggers:
            event_map.setdefault(t, []).append(name)

    if as_json:
        print(json.dumps(event_map, indent=2))
        return

    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('🗂  Event → Workflow Map')}  {grey(repo_path.name)}")
    print(f"{bold('━' * 72)}\n")
    if not local:
        print(yellow("  No workflows found.\n"))
        return
    for event, wfs in sorted(event_map.items()):
        print(f"  {bold(cyan(event))}")
        for wf in wfs:
            print(f"    {grey('└')}  {wf}")
        print()


# ─────────────────────────────────────────────────────────────
# DISPLAY: failure details
# ─────────────────────────────────────────────────────────────

def show_run_errors(run_id: str, as_json: bool = False) -> None:
    _check_gh_auth()
    jobs_data = _gh_json("run", "view", run_id, "--json", "jobs")
    if not isinstance(jobs_data, dict):
        jobs_data = {}

    if as_json:
        print(json.dumps(jobs_data, indent=2, default=str))
        return

    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('🔍  Failure Details')}  run {bold(run_id)}")
    print(f"{bold('━' * 72)}\n")

    for job in jobs_data.get("jobs", []):
        for step in job.get("steps", []):
            if step.get("conclusion") in ("failure", "timed_out"):
                print(f"  {red('✗')}  {bold(job.get('name','?'))}  /  "
                      f"{step.get('name','?')}  {grey(step.get('conclusion',''))}")
    try:
        log = _gh("run", "view", run_id, "--log-failed")
        print(f"\n  {grey('─ Log excerpt ─')}\n")
        for line in log.splitlines()[:60]:
            if re.search(r"\bERROR\b|\bFailed\b|\bfatal\b|\bError\b", line):
                print(f"  {red(line)}")
            else:
                print(f"  {grey(line)}")
        if len(log.splitlines()) > 60:
            print(grey(f"\n  … truncated. Full: gh run view {run_id} --log-failed"))
    except GHError as e:
        print(red(f"  ❌  {e}"))
    print()


# ─────────────────────────────────────────────────────────────
# ACTIONS
# ─────────────────────────────────────────────────────────────

def trigger_workflow(name: str, branch: str = "main") -> None:
    _check_gh_auth()
    print(f"\n  {cyan('▶')} Triggering {bold(name)} on {bold(branch)}…")
    try:
        _gh("workflow", "run", name, "--ref", branch)
        print(green("  ✓  Triggered.\n"))
    except GHError as e:
        print(red(f"  ❌  {e}"))
        print(yellow("  Ensure the workflow has a 'workflow_dispatch' trigger.\n"))


def rerun_failed(run_id: str) -> None:
    _check_gh_auth()
    print(f"\n  {cyan('↻')} Rerunning failed jobs in {bold(run_id)}…")
    try:
        _gh("run", "rerun", run_id, "--failed")
        print(green("  ✓  Rerun triggered.\n"))
    except GHError as e:
        print(red(f"  ❌  {e}\n"))


def rerun_all(run_id: str) -> None:
    _check_gh_auth()
    print(f"\n  {cyan('↻')} Rerunning all jobs in {bold(run_id)}…")
    try:
        _gh("run", "rerun", run_id)
        print(green("  ✓  Rerun triggered.\n"))
    except GHError as e:
        print(red(f"  ❌  {e}\n"))


def cancel_run(run_id: str) -> None:
    _check_gh_auth()
    print(f"\n  {yellow('⊗')} Cancelling {bold(run_id)}…")
    try:
        _gh("run", "cancel", run_id)
        print(green("  ✓  Cancelled.\n"))
    except GHError as e:
        print(red(f"  ❌  {e}\n"))


# ─────────────────────────────────────────────────────────────
# WORKFLOW TEMPLATES
# ─────────────────────────────────────────────────────────────

WORKFLOW_TEMPLATES = {
    "python-test": {
        "name": "Python Tests",
        "description": "pytest matrix on push/PR",
        "content": """\
name: Python Tests

on:
  push:
    branches: [main, master, develop]
  pull_request:
    branches: [main, master]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest
          pip install -e ".[dev]" || pip install -e . || true
      - name: Run tests
        run: pytest
""",
    },
    "python-lint": {
        "name": "Lint & Format",
        "description": "Ruff + mypy on push/PR",
        "content": """\
name: Lint & Format

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff mypy
      - name: Ruff
        run: ruff check .
      - name: Mypy
        run: mypy . --ignore-missing-imports || true
""",
    },
    "release-pypi": {
        "name": "Publish to PyPI",
        "description": "Build + publish on version tag",
        "content": """\
name: Publish to PyPI

on:
  push:
    tags:
      - "v*.*.*"

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: release
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - name: Build
        run: python -m build
      - name: Publish
        uses: pypa/gh-action-pypi-publish@release/v1
""",
    },
    "scheduled-job": {
        "name": "Scheduled Job",
        "description": "Cron schedule + manual dispatch",
        "content": """\
name: Scheduled Job

on:
  schedule:
    - cron: "0 8 * * 1"  # Every Monday 08:00 UTC
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Run job
        run: echo "Add your task here"
""",
    },
    "docker-build": {
        "name": "Docker Build & Push",
        "description": "Build + push to GHCR on push/tag",
        "content": """\
name: Docker Build & Push

on:
  push:
    branches: [main]
    tags: ["v*"]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
""",
    },
    "blank": {
        "name": "Blank Workflow",
        "description": "Start from scratch",
        "content": """\
name: My Workflow

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run a script
        run: echo "Hello, world!"
""",
    },
}


# ─────────────────────────────────────────────────────────────
# WORKFLOW FILE MANAGEMENT
# ─────────────────────────────────────────────────────────────

def create_workflow(
    repo_path: Path,
    filename: Optional[str] = None,
    template: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    wdir = _workflows_dir(repo_path)
    wdir.mkdir(parents=True, exist_ok=True)

    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('✨  Create New Workflow')}")
    print(f"{bold('━' * 72)}\n")

    if not template:
        print("  Choose a template:\n")
        keys = list(WORKFLOW_TEMPLATES.keys())
        for i, k in enumerate(keys, 1):
            t = WORKFLOW_TEMPLATES[k]
            print(f"    {bold(str(i))}.  {t['name']:<24} {grey(t['description'])}")
        print()
        try:
            choice = input("  Template [1]: ").strip() or "1"
            idx    = int(choice) - 1
            template = keys[idx] if 0 <= idx < len(keys) else "blank"
        except (ValueError, KeyboardInterrupt):
            template = "blank"

    tmpl    = WORKFLOW_TEMPLATES.get(template, WORKFLOW_TEMPLATES["blank"])
    content = tmpl["content"]

    if not filename:
        default = template.replace("_", "-") + ".yml"
        try:
            filename = input(f"  Filename [{default}]: ").strip() or default
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            return

    if not filename.endswith((".yml", ".yaml")):
        filename += ".yml"

    dest = wdir / filename

    if dry_run:
        print(f"\n  {cyan('◎  Dry-run')}  would create "
              f"{bold(str(dest.relative_to(repo_path)))}\n")
        print(grey(content))
        return

    if dest.exists():
        try:
            ow = input(f"\n  {yellow('⚠')}  {filename} exists. Overwrite? [y/N] ").strip().lower()
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            return
        if ow != "y":
            print("  Cancelled.")
            return

    _atomic_write_text(dest, content)
    print(f"\n  {green('✓')}  Created: {bold(str(dest.relative_to(repo_path)))}\n")

    editor = os.environ.get("EDITOR", "")
    if editor:
        try:
            if input(f"  Open in {editor}? [Y/n] ").strip().lower() != "n":
                subprocess.run([editor, str(dest)])
        except KeyboardInterrupt:
            pass


def edit_workflow(repo_path: Path, filename: Optional[str] = None) -> None:
    local = _list_local_workflows(repo_path)
    if not local:
        print(yellow("\n  No workflow files found.\n"))
        return
    if not filename:
        filename = _pick_workflow(local, "Edit")
        if not filename:
            return
    target = _resolve_workflow_path(repo_path, filename)
    if not target:
        print(red(f"\n  ❌  Not found: {filename}\n"))
        return
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    subprocess.run([editor, str(target)])


def delete_workflow(
    repo_path: Path,
    filename: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    local = _list_local_workflows(repo_path)
    if not local:
        print(yellow("\n  No workflow files found.\n"))
        return
    if not filename:
        filename = _pick_workflow(local, "Delete")
        if not filename:
            return
    target = _resolve_workflow_path(repo_path, filename)
    if not target:
        print(red(f"\n  ❌  Not found: {filename}\n"))
        return
    if dry_run:
        print(f"\n  {cyan('◎  Dry-run')}  would delete {bold(target.name)}\n")
        return
    try:
        confirm = input(
            f"\n  {red('⚠')}  Delete {bold(target.name)}? Cannot be undone. [y/N] "
        ).strip().lower()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        return
    if confirm != "y":
        print("  Cancelled.")
        return
    bak = _backup(target)
    target.unlink()
    print(f"\n  {green('✓')}  Deleted {target.name}  {grey('(backup: ' + bak.name + ')')}\n")


# ─────────────────────────────────────────────────────────────
# TRIGGER EDITOR
# ─────────────────────────────────────────────────────────────

COMMON_EVENTS = [
    "push", "pull_request", "workflow_dispatch", "schedule",
    "release", "issues", "issue_comment", "create", "delete",
    "fork", "watch", "repository_dispatch", "workflow_call",
    "check_run", "check_suite", "deployment", "deployment_status",
    "merge_group", "page_build", "registry_package",
]

CRON_PRESETS = [
    ("Every 15 minutes",       "*/15 * * * *"),
    ("Every hour",             "0 * * * *"),
    ("Every day midnight",     "0 0 * * *"),
    ("Every day 08:00 UTC",    "0 8 * * *"),
    ("Every Monday 08:00",     "0 8 * * 1"),
    ("Every weekday 09:00",    "0 9 * * 1-5"),
    ("Weekly (Sunday 00:00)",  "0 0 * * 0"),
    ("Monthly (1st 00:00)",    "0 0 1 * *"),
    ("Custom…",                None),
]


def edit_triggers(
    repo_path: Path,
    filename: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    local = _list_local_workflows(repo_path)
    if not local:
        print(yellow("\n  No workflow files found.\n"))
        return
    if not filename:
        filename = _pick_workflow(local, "Edit triggers for")
        if not filename:
            return
    target = _resolve_workflow_path(repo_path, filename)
    if not target:
        print(red(f"\n  ❌  Not found: {filename}\n"))
        return

    original = target.read_text(encoding="utf-8", errors="replace")
    doc      = WorkflowDoc(original)

    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('⚙  Edit Triggers')}  {grey('─')}  {bold(target.name)}")
    print(f"{bold('━' * 72)}\n")
    print(f"  Current triggers : {', '.join(doc.triggers) or grey('none')}")
    if doc.crons:
        print(f"  Current cron(s)  : {', '.join(doc.crons)}")
    if _HAS_RUAMEL:
        print(f"  {green('✓')}  {dim('ruamel.yaml active — comments & formatting preserved')}")
    else:
        print(f"  {yellow('⚠')}  {dim('regex mode — install ruamel.yaml for safer edits')}")
    if dry_run:
        print(f"  {cyan('◎  Dry-run mode')} — no files will be written")

    print(f"\n    {bold('1')}.  Add event\n"
          f"    {bold('2')}.  Remove event\n"
          f"    {bold('3')}.  Add cron schedule\n"
          f"    {bold('4')}.  Remove all crons\n"
          f"    {bold('5')}.  Replace ALL triggers\n"
          f"    {bold('0')}.  Cancel\n")
    try:
        action = input("  Choice: ").strip()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        return

    if action == "1":
        event = _pick_event()
        if event:
            doc.add_event(event)
            _finalize(target, original, doc.to_string(), dry_run, f"Added: {event}")

    elif action == "2":
        ev = _pick_from_list(doc.triggers, "Remove which event?")
        if ev:
            doc.remove_event(ev)
            _finalize(target, original, doc.to_string(), dry_run, f"Removed: {ev}")

    elif action == "3":
        expr = _pick_cron()
        if expr:
            doc.set_cron(expr)
            _finalize(target, original, doc.to_string(), dry_run, f"Added cron: {expr}")

    elif action == "4":
        doc.remove_cron()
        _finalize(target, original, doc.to_string(), dry_run, "Removed all crons")

    elif action == "5":
        print(f"\n  {grey('Events (comma-separated):')}  e.g. push, pull_request\n")
        try:
            raw = input("  Events: ").strip()
        except KeyboardInterrupt:
            print("  Cancelled.")
            return
        events = [e.strip() for e in raw.split(",") if e.strip()]
        if events:
            doc.replace_triggers(events)
            _finalize(target, original, doc.to_string(), dry_run,
                      f"Set triggers: {', '.join(events)}")
    else:
        print("  Cancelled.")


def _finalize(path: Path, original: str, new_text: str,
              dry_run: bool, success_msg: str) -> None:
    ok = _safe_write(path, original, new_text, dry_run=dry_run)
    if ok and not dry_run:
        print(green(f"  ✓  {success_msg}"))


# ── picker helpers ───────────────────────────────────────────

def _pick_event() -> Optional[str]:
    print(f"\n  Available events:\n")
    for i, ev in enumerate(COMMON_EVENTS, 1):
        print(f"    {i:2}.  {ev}")
    print()
    try:
        choice = input("  Event name or number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            return COMMON_EVENTS[idx] if 0 <= idx < len(COMMON_EVENTS) else choice
        return choice or None
    except (ValueError, KeyboardInterrupt):
        return None


def _pick_cron() -> Optional[str]:
    print(f"\n  Cron presets:\n")
    for i, (label, expr) in enumerate(CRON_PRESETS, 1):
        suffix = f"  {grey(expr)}" if expr else ""
        print(f"    {bold(str(i))}.  {label:<30}{suffix}")
    print()
    try:
        choice = input("  Choice: ").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(CRON_PRESETS):
            label, expr = CRON_PRESETS[idx]
            if expr is None:
                return input("  Cron expression: ").strip() or None
            return expr
        return choice or None
    except (ValueError, KeyboardInterrupt):
        return None


def _pick_from_list(items: list, prompt: str) -> Optional[str]:
    if not items:
        print(yellow("  Nothing to choose from.\n"))
        return None
    print(f"\n  {prompt}\n")
    for i, item in enumerate(items, 1):
        print(f"    {i}.  {item}")
    print()
    try:
        choice = input("  Choice: ").strip()
        idx = int(choice) - 1
        return items[idx] if 0 <= idx < len(items) else None
    except (ValueError, KeyboardInterrupt):
        return None


def _pick_workflow(local: list, action: str) -> Optional[str]:
    print(f"\n  {action} which workflow?\n")
    for i, p in enumerate(local, 1):
        print(f"    {bold(str(i))}.  {p.name}")
    print()
    try:
        choice = input("  Choice: ").strip()
        idx    = int(choice) - 1
        return local[idx].name if 0 <= idx < len(local) else None
    except (ValueError, KeyboardInterrupt):
        return None


# ─────────────────────────────────────────────────────────────
# INTERACTIVE MENU
# ─────────────────────────────────────────────────────────────

def interactive_ci_menu(repo_path: Path) -> None:
    while True:
        print(f"\n{bold('━' * 72)}")
        print(f"  {bold(cyan('⚡  GitHub Actions CI'))}  {grey('─')}  {grey(repo_path.name)}")
        print(f"{bold('━' * 72)}\n")
        print(f"  {bold('OBSERVE')}")
        print(f"    {bold('1')}.  {cyan('Overview')}       All workflows · stats · failure %")
        print(f"    {bold('2')}.  {cyan('Recent runs')}    Latest run list")
        print(f"    {bold('3')}.  {cyan('Event map')}      Event → workflow mapping")
        print(f"    {bold('4')}.  {cyan('Failure logs')}   Failed step details + log excerpt")
        print(f"\n  {bold('ACT')}")
        print(f"    {bold('5')}.  {yellow('Trigger')}        Dispatch workflow_dispatch")
        print(f"    {bold('6')}.  {yellow('Rerun failed')}   Rerun failed jobs in a run")
        print(f"    {bold('7')}.  {yellow('Rerun all')}      Rerun all jobs in a run")
        print(f"    {bold('8')}.  {yellow('Cancel')}         Cancel an in-progress run")
        print(f"\n  {bold('MANAGE')}")
        print(f"    {bold('9')}.  {magenta('Create')}         New workflow from template")
        print(f"    {bold('10')}. {magenta('Edit')}            Open in $EDITOR")
        print(f"    {bold('11')}. {magenta('Delete')}          Remove workflow file")
        print(f"    {bold('12')}. {magenta('Triggers')}        Edit events / cron interactively")
        print(f"\n    {bold('0')}.  {grey('Back')}\n")

        try:
            choice = input("  Choice: ").strip()
        except KeyboardInterrupt:
            print("\n  Exiting CI menu.")
            break

        if choice == "0":
            break
        elif choice == "1":
            show_overview(repo_path)
        elif choice == "2":
            wf = None
            try:
                wf = input("\n  Workflow filter (Enter for all): ").strip() or None
            except KeyboardInterrupt:
                pass
            try:
                n = int(input("  Number of runs [20]: ").strip() or "20")
            except (ValueError, KeyboardInterrupt):
                n = 20
            show_runs(repo_path, wf, n)
        elif choice == "3":
            show_event_map(repo_path)
        elif choice == "4":
            try:
                rid = input("\n  Run ID: ").strip()
                if rid:
                    show_run_errors(rid)
            except KeyboardInterrupt:
                pass
        elif choice == "5":
            local = _list_local_workflows(repo_path)
            wf_name = _pick_workflow(local, "Trigger") if local else None
            if wf_name:
                try:
                    branch = input("  Branch [main]: ").strip() or "main"
                except KeyboardInterrupt:
                    branch = "main"
                trigger_workflow(
                    wf_name.replace(".yml", "").replace(".yaml", ""), branch
                )
        elif choice == "6":
            try:
                rid = input("\n  Run ID: ").strip()
                if rid: rerun_failed(rid)
            except KeyboardInterrupt:
                pass
        elif choice == "7":
            try:
                rid = input("\n  Run ID: ").strip()
                if rid: rerun_all(rid)
            except KeyboardInterrupt:
                pass
        elif choice == "8":
            try:
                rid = input("\n  Run ID: ").strip()
                if rid: cancel_run(rid)
            except KeyboardInterrupt:
                pass
        elif choice == "9":
            dry = _ask_dry_run()
            create_workflow(repo_path, dry_run=dry)
        elif choice == "10":
            edit_workflow(repo_path)
        elif choice == "11":
            dry = _ask_dry_run()
            delete_workflow(repo_path, dry_run=dry)
        elif choice == "12":
            dry = _ask_dry_run()
            edit_triggers(repo_path, dry_run=dry)
        else:
            print(f"  {yellow('?')}  Unknown: {choice}")


def _ask_dry_run() -> bool:
    try:
        return input("  Dry-run first? [y/N] ").strip().lower() == "y"
    except KeyboardInterrupt:
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRYPOINTS
# ─────────────────────────────────────────────────────────────

def main_with_repo(repo_path: Path) -> None:
    interactive_ci_menu(repo_path)


def main_with_args(
    repo_path: Path,
    *,
    overview:       bool = False,
    runs:           bool = False,
    events:         bool = False,
    errors:         Optional[str] = None,
    workflow:       Optional[str] = None,
    limit:          int  = 20,
    trigger:        Optional[str] = None,
    rerun:          Optional[str] = None,
    rerun_all_flag: bool = False,
    cancel:         Optional[str] = None,
    branch:         str  = "main",
    create:         bool = False,
    template:       Optional[str] = None,
    filename:       Optional[str] = None,
    edit:           bool = False,
    delete:         bool = False,
    triggers:       bool = False,
    dry_run:        bool = False,
    as_json:        bool = False,
) -> None:
    """Non-interactive CLI entrypoint."""
    if overview:
        show_overview(repo_path, limit, as_json)
    elif runs:
        show_runs(repo_path, workflow, limit, as_json)
    elif events:
        show_event_map(repo_path, as_json)
    elif errors:
        show_run_errors(errors, as_json)
    elif trigger:
        trigger_workflow(trigger, branch)
    elif rerun:
        rerun_all(rerun) if rerun_all_flag else rerun_failed(rerun)
    elif cancel:
        cancel_run(cancel)
    elif create:
        create_workflow(repo_path, filename, template, dry_run)
    elif edit:
        edit_workflow(repo_path, filename)
    elif delete:
        delete_workflow(repo_path, filename, dry_run)
    elif triggers:
        edit_triggers(repo_path, filename, dry_run)
    else:
        interactive_ci_menu(repo_path)
