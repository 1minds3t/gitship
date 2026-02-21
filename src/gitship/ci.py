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
    # Prefer sensible editors over vi which breaks terminals
    editor = (
        os.environ.get("EDITOR")
        or os.environ.get("VISUAL")
        or _find_usable_editor()
    )
    subprocess.run([editor, str(target)])

def _find_usable_editor() -> str:
    """Find a usable editor, preferring ones that don't destroy terminals."""
    candidates = ["nano", "micro", "code", "subl", "gedit", "notepad", "vi"]
    for editor in candidates:
        if shutil.which(editor):
            return editor
    return "vi"  # last resort

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
# REGRESSION DIFF  (last-pass vs HEAD, branch-aware, hunk-level)
# ─────────────────────────────────────────────────────────────

"""
How branch resolution works
───────────────────────────
GitHub Actions runs are tied to a specific branch (headBranch) and commit
(headSha).  When finding the "last passing run" we must restrict to the SAME
branch that is currently checked out, because:

  • A push workflow on `main` tests `main` code.
  • A PR workflow on `feature/xyz` tests that feature branch.
  • A scheduled workflow always runs on the default branch.

We find the last successful run for the current local branch by matching
run.headBranch == local_branch.  We then diff run.headSha (the commit that
PASSED) against the local HEAD commit on that branch.

If the last passing commit is not in the local repo (e.g. shallow clone or the
user is on a fork), we fetch it transparently.
"""


def _local_head_sha(repo_path: Path) -> str:
    """Return the SHA of the local HEAD commit."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def _local_branch(repo_path: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else "HEAD"


def _ensure_sha_fetched(repo_path: Path, sha: str) -> bool:
    """Return True if sha is already known locally; fetch origin if not."""
    r = subprocess.run(
        ["git", "cat-file", "-e", sha],
        cwd=repo_path, capture_output=True,
    )
    if r.returncode == 0:
        return True
    print(grey(f"  Fetching {sha[:8]} from origin…"))
    r2 = subprocess.run(
        ["git", "fetch", "origin", sha],
        cwd=repo_path, capture_output=True,
    )
    return r2.returncode == 0


def _git_diff_files(repo_path: Path, base_sha: str, head_sha: str) -> list[str]:
    """Return list of files that differ between base_sha and head_sha."""
    r = subprocess.run(
        ["git", "diff", "--name-only", base_sha, head_sha],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []
    return [f.strip() for f in r.stdout.splitlines() if f.strip()]


def _git_diff_stat(repo_path: Path, base_sha: str, head_sha: str) -> dict:
    """
    Return per-file diff stats: insertions, deletions, commit count.

    Returns dict keyed by filepath:
        { "ins": int, "del": int, "commits": int }
    """
    stats: dict = {}

    # --numstat gives machine-readable ins/del per file
    r = subprocess.run(
        ["git", "diff", "--numstat", base_sha, head_sha],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                ins_raw, del_raw, filepath = parts[0], parts[1], parts[2].strip()
                try:
                    stats[filepath] = {
                        "ins": int(ins_raw) if ins_raw != "-" else 0,
                        "del": int(del_raw) if del_raw != "-" else 0,
                        "commits": 0,
                    }
                except ValueError:
                    stats[filepath] = {"ins": 0, "del": 0, "commits": 0}

    # Count commits touching each file in the range
    r2 = subprocess.run(
        ["git", "log", "--format=%H", "--name-only",
         f"{base_sha}..{head_sha}"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r2.returncode == 0:
        current_commit = None
        seen: dict = {}   # filepath -> set of commit SHAs
        for line in r2.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
                current_commit = line
            elif current_commit:
                seen.setdefault(line, set()).add(current_commit)
        for filepath, shas in seen.items():
            if filepath in stats:
                stats[filepath]["commits"] = len(shas)
            else:
                stats[filepath] = {"ins": 0, "del": 0, "commits": len(shas)}

    return stats


def _git_diff_file(repo_path: Path, base_sha: str, head_sha: str, filepath: str) -> str:
    """Return the full unified diff text for a single file."""
    r = subprocess.run(
        ["git", "diff", "-U5", base_sha, head_sha, "--", filepath],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def _parse_hunks(diff_text: str) -> list[dict]:
    """
    Split a unified diff into individual hunks.

    Returns a list of dicts:
        { "header": str,        # @@ -a,b +c,d @@ context
          "lines": list[str],   # raw diff lines (no newlines stripped)
          "old_start": int,
          "new_start": int,
          "old_file": str,
          "new_file": str }
    """
    hunks: list[dict] = []
    current: dict | None = None
    old_file = new_file = ""

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("--- "):
            old_file = line[4:].strip().removeprefix("a/")
        elif line.startswith("+++ "):
            new_file = line[4:].strip().removeprefix("b/")
        elif line.startswith("@@ "):
            if current:
                hunks.append(current)
            # parse @@ -old_start[,old_len] +new_start[,new_len] @@
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            old_start = int(m.group(1)) if m else 0
            new_start = int(m.group(2)) if m else 0
            current = {
                "header": line.rstrip(),
                "lines": [line],
                "old_start": old_start,
                "new_start": new_start,
                "old_file": old_file,
                "new_file": new_file,
            }
        elif current is not None:
            current["lines"].append(line)

    if current:
        hunks.append(current)
    return hunks


def _git_show_file(repo_path, sha: str, filepath: str) -> str:
    """Return full file content at a given commit SHA. Empty string on failure."""
    r = subprocess.run(
        ["git", "show", f"{sha}:{filepath}"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def _make_search_pattern(query: str) -> str:
    """
    Turn a user query into a regex tolerant of dynamic values.

    Key insight: user pastes from CI log output (e.g. "Processing 66 packages")
    but the source file has a placeholder (e.g. "Processing {} packages").
    Numbers in the query become wildcard tokens to bridge this gap.

    Also handles: {name} placeholders, %s/%d format specs, flexible whitespace.
    """
    query = query.strip()
    # Replace Python format placeholders with sentinel
    query = re.sub(r"\{[^}]*\}", "WILDCARD", query)
    query = re.sub(r"%[sdrf]", "WILDCARD", query)
    # Replace standalone numbers with wildcard (CI log value vs source placeholder)
    query = re.sub(r"\b\d+\b", "WILDCARD", query)
    # Build pattern token by token
    parts = re.split(r"(WILDCARD)", query)
    pat_parts = []
    for part in parts:
        if part == "WILDCARD":
            pat_parts.append(r"\S*")
        elif part:
            escaped = re.escape(part)
            # Spaces become flexible whitespace
            escaped = escaped.replace(r"\ ", r"\s+")
            pat_parts.append(escaped)
    return "".join(pat_parts)


def _find_enclosing_function(lines: list, target_lineno: int) -> tuple:
    """
    Given file lines (1-indexed) and a target line number,
    return (func_name, func_start, func_end, class_name).

    Walks backward to find the nearest enclosing def, then the enclosing class.
    Returns ("", 0, 0, "") if not found.
    """
    if not lines or target_lineno < 1:
        return ("", 0, 0, "")

    target_idx = min(target_lineno - 1, len(lines) - 1)

    # Walk backward to find enclosing def
    func_indent = None
    func_start = 0
    func_name = ""

    for i in range(target_idx, -1, -1):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        m = re.match(r"(?:async\s+)?def\s+(\w+)\s*\(", stripped)
        if m:
            if func_indent is None or indent <= func_indent:
                func_indent = indent
                func_start = i + 1   # 1-based
                func_name = m.group(1)
                break

    if not func_name:
        return ("", 0, 0, "")

    # Walk forward to find end of function
    func_end = len(lines)
    for i in range(func_start, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        if indent <= func_indent and i > func_start - 1:
            if re.match(r"(?:async\s+)?def\s+|class\s+", stripped):
                func_end = i
                break

    # Walk backward from func_start to find enclosing class
    class_name = ""
    if func_indent is not None and func_indent > 0:
        for i in range(func_start - 2, -1, -1):
            line = lines[i]
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)
            if indent < func_indent:
                mc = re.match(r"class\s+(\w+)", stripped)
                if mc:
                    class_name = mc.group(1)
                break

    return (func_name, func_start, func_end, class_name)


def _hunks_in_line_range(hunks: list, start_line: int, end_line: int) -> list:
    """
    Return indices of hunks whose new_start falls within [start_line, end_line],
    OR whose context lines overlap the range.
    """
    result = []
    for idx, hunk in enumerate(hunks):
        hunk_new_end = hunk["new_start"] + sum(
            1 for l in hunk["lines"] if not l.startswith("-")
        )
        # overlaps if hunk starts before range end AND hunk ends after range start
        if hunk["new_start"] <= end_line and hunk_new_end >= start_line:
            result.append(idx)
    return result


def _search_across_files(
    query: str,
    all_file_hunks: dict,   # filepath -> (diff_text, list[hunk])
    repo_path,
    head_sha: str,
) -> list:
    """
    True Ctrl+F search: find the query string in the actual file content at HEAD,
    locate the enclosing function, return all hunks within that function plus
    hunks in other files that reference the function name.

    Returns list of result dicts:
        { filepath, hunk, hunk_idx, total_hunks, diff_text,
          reason, func_name, match_line }
    Grouped: direct function hunks first, then cross-file caller hunks.
    """
    query = query.strip()
    if not query:
        return []

    # Build a fuzzy pattern that handles dynamic numbers and {} placeholders
    try:
        pattern = _make_search_pattern(query)
        pat_re = re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Fallback: plain substring
        pat_re = re.compile(re.escape(query), re.IGNORECASE)

    results = []
    # func_name -> {"files": set, "class_name": str}
    found_funcs = {}

    for filepath, (diff_text, hunks) in all_file_hunks.items():
        # Fetch full file at HEAD so we can search ALL lines, not just diff context
        file_content = _git_show_file(repo_path, head_sha, filepath)
        if not file_content:
            continue
        file_lines = file_content.splitlines()

        match_linenos = []
        for lineno, line in enumerate(file_lines, 1):
            if pat_re.search(line):
                match_linenos.append(lineno)

        if not match_linenos:
            continue

        for match_lineno in match_linenos:
            func_name, func_start, func_end, class_name = _find_enclosing_function(
                file_lines, match_lineno
            )

            # Find all hunks that touch this function's body
            if func_start:
                hunk_idxs = _hunks_in_line_range(hunks, func_start, func_end)
            else:
                best_idx = None
                best_dist = float("inf")
                for idx, hunk in enumerate(hunks):
                    dist = abs(hunk["new_start"] - match_lineno)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = idx
                hunk_idxs = [best_idx] if best_idx is not None else []

            already_added = {r["hunk_idx"] for r in results
                             if r["filepath"] == filepath}

            for idx in hunk_idxs:
                if idx in already_added:
                    continue
                hunk = hunks[idx]
                scope = f"{class_name}.{func_name}" if class_name else func_name
                reason = f"inside {scope}()" if func_name else f"near line {match_lineno}"
                results.append({
                    "filepath": filepath,
                    "hunk": hunk,
                    "hunk_idx": idx,
                    "total_hunks": len(hunks),
                    "diff_text": diff_text,
                    "reason": reason,
                    "func_name": func_name,
                    "class_name": class_name,
                    "match_line": match_lineno,
                    "priority": 0,
                })
                if func_name:
                    found_funcs.setdefault(func_name, {"files": set(), "class_name": class_name})
                    found_funcs[func_name]["files"].add(filepath)

    # Cross-file callers — only match if the call is class-scoped (avoids false positives
    # on common names like run/start/execute that appear everywhere).
    # Strategy: require ClassName.method( OR instance = ClassName(); instance.method()
    # For methods with a class, we look for patterns like:
    #   SomeClass(...).run(  /  obj.run(  only if "SomeClass" also appears nearby in hunk
    # For module-level functions (no class), a direct name match is fine.
    if found_funcs:
        for filepath, (diff_text, hunks) in all_file_hunks.items():
            for func_name, info in found_funcs.items():
                if filepath in info["files"]:
                    continue
                class_name = info["class_name"]
                already_added = {r["hunk_idx"] for r in results
                                 if r["filepath"] == filepath}
                for idx, hunk in enumerate(hunks):
                    if idx in already_added:
                        continue
                    body = "".join(hunk["lines"])
                    matched_as_caller = False
                    if class_name:
                        # Only match if the class name also appears in this hunk
                        # (e.g. hunk instantiates or imports the class)
                        if (re.search(rf"\b{re.escape(class_name)}\b", body) and
                                re.search(rf"\.{re.escape(func_name)}\s*\(", body)):
                            matched_as_caller = True
                    else:
                        # Module-level function: direct call is fine
                        if re.search(rf"\b{re.escape(func_name)}\s*\(", body):
                            matched_as_caller = True
                    if matched_as_caller:
                        scope = f"{class_name}.{func_name}" if class_name else func_name
                        results.append({
                            "filepath": filepath,
                            "hunk": hunk,
                            "hunk_idx": idx,
                            "total_hunks": len(hunks),
                            "diff_text": diff_text,
                            "reason": f"calls {scope}()",
                            "func_name": func_name,
                            "class_name": class_name,
                            "match_line": 0,
                            "priority": 1,
                        })

    # Sort: direct matches first (priority 0), then callers (priority 1)
    results.sort(key=lambda x: (x["priority"], x["filepath"], x["hunk_idx"]))
    return results


def _print_hunk(hunk: dict, index: int, total: int) -> None:
    """Pretty-print a single diff hunk with colour."""
    print(f"\n  {bold(cyan(f'Hunk {index}/{total}'))}  "
          f"{grey(hunk['old_file'])}  {cyan(hunk['header'])}\n")
    for line in hunk["lines"][1:]:   # skip the @@ header line we already printed
        raw = line.rstrip("\n")
        if raw.startswith("+"):
            print(green(f"  {raw}"))
        elif raw.startswith("-"):
            print(red(f"  {raw}"))
        else:
            print(grey(f"  {raw}"))


def _apply_patch_lines(repo_path: Path, patch_text: str) -> bool:
    """Apply a patch via `git apply --index`."""
    r = subprocess.run(
        ["git", "apply", "--index", "-"],
        input=patch_text, text=True,
        cwd=repo_path, capture_output=True,
    )
    if r.returncode != 0:
        print(red(f"  ✗  git apply failed: {r.stderr.strip()}"))
        return False
    return True


def _build_revert_patch(diff_text: str, selected_hunks: list[dict]) -> str:
    """
    Build a patch that reverts only the selected hunks (swap +/- lines).

    We reconstruct a valid unified diff with inverted +/- so that
    `git apply` will restore the file to the base-sha state for those hunks.
    """
    lines = diff_text.splitlines(keepends=True)

    # Collect the file header lines (---, +++)
    header_lines: list[str] = []
    for line in lines:
        if line.startswith("diff ") or line.startswith("index "):
            header_lines.append(line)
        elif line.startswith("--- "):
            # Swap: the revert patch's --- is the current HEAD version (+++ in orig)
            # We'll fix this after we know the filenames
            header_lines.append(line)
        elif line.startswith("+++ "):
            header_lines.append(line)
            break

    # Swap --- and +++ lines for the inverted patch
    swapped_header: list[str] = []
    minus_line = plus_line = ""
    for hl in header_lines:
        if hl.startswith("--- "):
            minus_line = hl
        elif hl.startswith("+++ "):
            plus_line = hl
        else:
            swapped_header.append(hl)
    # Inverted: old becomes b/ (HEAD), new becomes a/ (base)
    if minus_line and plus_line:
        swapped_header.append(plus_line.replace("+++ b/", "--- a/").replace("+++ ", "--- "))
        swapped_header.append(minus_line.replace("--- a/", "+++ b/").replace("--- ", "+++ "))

    patch_parts = ["".join(swapped_header)]

    for hunk in selected_hunks:
        # Invert the hunk: swap + and - lines, recalculate header counts
        inverted: list[str] = []
        old_count = new_count = 0
        for line in hunk["lines"][1:]:  # skip @@ header
            if line.startswith("+"):
                inverted.append("-" + line[1:])
                old_count += 1
            elif line.startswith("-"):
                inverted.append("+" + line[1:])
                new_count += 1
            else:
                inverted.append(line)
                old_count += 1
                new_count += 1
        # Rebuild @@ header: in inverted patch, old_start == hunk's new_start
        new_header = f"@@ -{hunk['new_start']},{old_count} +{hunk['old_start']},{new_count} @@"
        # Preserve any trailing context description from original @@ line
        tail = re.sub(r"^@@ -\S+ \+\S+ @@", "", hunk["header"])
        patch_parts.append(new_header + tail + "\n")
        patch_parts.extend(inverted)

    return "".join(patch_parts)


def _find_last_passing_run(
    repo_path: Path,
    workflow_name: Optional[str],
    branch: str,
) -> Optional[dict]:
    """
    Return the most recent successful run for (workflow, branch).

    Branch matching logic:
    - For push/pull_request workflows: must match headBranch == branch
    - For schedule workflows: uses the default branch; we still filter by branch
      so that `main` scheduled runs are found when user is on main.
    """
    cmd = [
        "run", "list",
        "--limit", "100",
        "--json", "databaseId,workflowName,conclusion,status,headBranch,"
                  "headSha,createdAt,event,url",
    ]
    if workflow_name:
        cmd += ["--workflow", workflow_name]

    runs = _cached_gh_json(repo_path, *cmd, ttl=30)

    # Filter: success + matching branch
    passing = [
        r for r in runs
        if r.get("conclusion") == "success"
        and r.get("headBranch") == branch
    ]

    return passing[0] if passing else None


def _find_last_failing_run(
    repo_path: Path,
    workflow_name: Optional[str],
    branch: str,
) -> Optional[dict]:
    cmd = [
        "run", "list",
        "--limit", "50",
        "--json", "databaseId,workflowName,conclusion,status,headBranch,"
                  "headSha,createdAt,event,url",
    ]
    if workflow_name:
        cmd += ["--workflow", workflow_name]

    runs = _cached_gh_json(repo_path, *cmd, ttl=30)

    failing = [
        r for r in runs
        if r.get("conclusion") in ("failure", "timed_out")
        and r.get("headBranch") == branch
    ]
    return failing[0] if failing else None


def _pick_workflow_for_regression(repo_path: Path) -> Optional[tuple[str, str]]:
    """
    Let the user pick a workflow, then show last-pass info.
    Returns (workflow_name, branch) or None.
    """
    _check_gh_auth()
    local = _list_local_workflows(repo_path)
    branch = _local_branch(repo_path)

    print(f"\n{bold('━' * 72)}")
    print(f"  {bold(cyan('🔬  Regression Diff'))}  "
          f"{grey('last passing run → HEAD')}  {grey(f'branch: {branch}')}")
    print(f"{bold('━' * 72)}\n")

    if not local:
        print(yellow("  No local workflows found.\n"))
        return None

    print(f"  {'#':<4} {'WORKFLOW FILE':<30} {'LAST PASS':<22} {'LAST FAIL':<22}")
    _sep()

    all_runs = _cached_gh_json(
        repo_path,
        "run", "list", "--limit", "200",
        "--json", "workflowName,conclusion,status,headBranch,headSha,"
                  "createdAt,databaseId,event",
        ttl=30,
    )

    index = WorkflowIndex(repo_path, all_runs)
    entries: list[tuple[int, str, str, Optional[str], Optional[str]]] = []
    # (display_idx, wf_name_for_gh, wf_display, last_pass_age, last_fail_age)

    for i, wf in enumerate(local, 1):
        content  = wf.read_text(encoding="utf-8", errors="replace")
        doc      = WorkflowDoc(content)
        name     = doc.name or wf.stem
        wf_runs  = index.runs_for_file(wf, content)

        branch_runs = [r for r in wf_runs if r.get("headBranch") == branch]

        pass_run = next(
            (r for r in branch_runs if r.get("conclusion") == "success"), None
        )
        fail_run = next(
            (r for r in branch_runs if r.get("conclusion") in ("failure", "timed_out")),
            None,
        )

        pass_str = (green(_fmt_ago(pass_run["createdAt"])) if pass_run
                    else grey("never on this branch"))
        fail_str = (red(_fmt_ago(fail_run["createdAt"])) if fail_run
                    else grey("─"))

        print(f"  {bold(str(i)):<4} {name:<30} {pass_str:<30} {fail_str}")
        entries.append((i, wf.name.replace(".yml","").replace(".yaml",""), name,
                        pass_run["headSha"] if pass_run else None,
                        fail_run["headSha"] if fail_run else None))

    print()

    try:
        raw = input("  Workflow number (Enter to cancel): ").strip()
        if not raw:
            return None
        idx = int(raw) - 1
        if not (0 <= idx < len(entries)):
            print(red("  Invalid selection.\n"))
            return None
    except (ValueError, KeyboardInterrupt):
        return None

    _, wf_id, wf_display, pass_sha, fail_sha = entries[idx]

    if not pass_sha:
        print(yellow(f"\n  No passing run found for '{wf_display}' on branch '{branch}'.\n"))
        print(grey("  Tip: ensure the workflow has run successfully on this branch at least once."))
        return None

    return (wf_id, branch, pass_sha, fail_sha)   # type: ignore[return-value]


def show_regression_diff(repo_path: Path) -> None:
    """
    Interactive regression diff: last passing run → HEAD, per file, per hunk.

    Flow:
      1. Pick workflow  (with last-pass / last-fail timestamps shown per branch)
      2. Resolve base commit = last passing run's headSha  (branch-aware)
      3. Show files changed between base and HEAD
      4. User selects files to inspect
      5. For each file: show hunks one by one
         [k] Keep current (ours)  [r] Revert this hunk (theirs/base)
         [s] Skip file            [a] Revert all hunks in file
         [q] Quit
      6. All chosen reversions are applied via `git apply --index`
      7. Optional: create a commit with a descriptive message
    """
    _check_gh_auth()

    result = _pick_workflow_for_regression(repo_path)
    if result is None:
        return

    wf_id, branch, base_sha, fail_sha = result
    head_sha = _local_head_sha(repo_path)

    if not head_sha:
        print(red("  ✗  Could not determine local HEAD sha.\n"))
        return

    if base_sha == head_sha:
        print(green(f"\n  ✓  HEAD ({head_sha[:8]}) IS the last passing commit. "
                    "No regression diff to show.\n"))
        return

    print(f"\n  {cyan('◎')} Base (last pass):  {bold(green(base_sha[:8]))}  "
          f"{grey(f'({branch})')}")
    print(f"  {cyan('◎')} HEAD (current):    {bold(yellow(head_sha[:8]))}  "
          f"{grey(f'({branch})')}")

    # Ensure base sha is available locally
    if not _ensure_sha_fetched(repo_path, base_sha):
        print(red(f"  ✗  Could not fetch base commit {base_sha[:8]} from origin.\n"))
        return

    # Get changed files with stat info
    changed = _git_diff_files(repo_path, base_sha, head_sha)
    if not changed:
        print(green("\n  ✓  No file differences found between base and HEAD.\n"))
        return

    file_stats = _git_diff_stat(repo_path, base_sha, head_sha)

    print(f"\n  {bold(str(len(changed)))} file(s) changed since last passing run:\n")
    print(f"  {'#':<4} {'FILE':<52} {'CHANGES':>14}  {'COMMITS':>7}")
    _sep(char="·")
    for i, f in enumerate(changed, 1):
        st = file_stats.get(f, {})
        ins = st.get("ins", 0)
        del_ = st.get("del", 0)
        commits = st.get("commits", 0)
        ins_str = green(f"+{ins}") if ins else grey("+0")
        del_str = red(f"-{del_}") if del_ else grey("-0")
        chg_str = f"{ins_str} {del_str}"
        cmt_str = yellow(str(commits)) if commits > 1 else grey(str(commits))
        fname = f[-52:] if len(f) > 52 else f
        print(f"  {bold(str(i)):<4} {fname:<52} {chg_str:>22}  {cmt_str:>7}")
    print()

    print(f"  {grey('Select files by number (e.g. 1 3 5)  |  all  |  Enter to cancel')}")
    print(f"  {cyan('Search')}{grey(': type your query directly — function name, log line, or paste from traceback')}")
    print(f"  {grey('  e.g.  parallel processing     _worker     Processing 66 packages     line 282')}")
    _export_hint = 'export  to save full diff report to ' + str(_get_gitship_export_path())
    print(f"  {grey(_export_hint)}")
    try:
        raw = input("  Files / search / export: ").strip()
    except KeyboardInterrupt:
        return

    if not raw:
        return

    # Export mode: write full plain-text diff report for holistic analysis
    if raw.lower() in ("export", "e"):
        # Need all_file_hunks for the report — load them now
        _all_fh: dict = {}
        for _fp in changed:
            _dt = _git_diff_file(repo_path, base_sha, head_sha, _fp)
            if _dt:
                _hs = _parse_hunks(_dt)
                if _hs:
                    _all_fh[_fp] = (_dt, _hs)
        out = _export_regression_report(
            repo_path, base_sha, head_sha, branch, wf_id,
            changed, file_stats, _all_fh
        )
        if out:
            print(green(f"  ✓  Report saved: {out}"))
            print(grey("  Open it in any editor for holistic analysis, or paste to an LLM."))
        return

    # Detect search mode: starts with / or ?, OR is not purely numbers/keywords
    def _is_search(s: str) -> bool:
        if s.startswith("/") or s.startswith("?"):
            return True
        # not a number list and not "all" -> treat as search query
        if s.lower() == "all":
            return False
        parts = s.split()
        return not all(p.isdigit() for p in parts)

    if _is_search(raw):
        # strip leading / or ? sigil if present, strip "search" keyword if typed literally
        query = raw.lstrip("/?").strip()
        if query.lower().startswith("search "):
            query = query[7:].strip()
        if not query:
            try:
                query = input("  Search query: ").strip()
            except KeyboardInterrupt:
                return
        if not query:
            return

        # Load all hunks for all changed files
        print(grey("  Scanning files…"))
        all_file_hunks: dict = {}
        for filepath in changed:
            diff_text = _git_diff_file(repo_path, base_sha, head_sha, filepath)
            if not diff_text:
                continue
            hunks = _parse_hunks(diff_text)
            if hunks:
                all_file_hunks[filepath] = (diff_text, hunks)

        # True Ctrl+F search across full file content at HEAD
        # Finds the enclosing function, returns all hunks within it + callers
        results = _search_across_files(query, all_file_hunks, repo_path, head_sha)

        if not results:
            print(yellow(f"  No matches found for: {query!r}"))
            print(grey("  The string wasn't found in any changed file at HEAD."))
            print(grey("  Try: a shorter phrase, a function name, or check the file isn't excluded."))
            return

        direct = [r for r in results if r["priority"] == 0]
        callers = [r for r in results if r["priority"] == 1]

        func_names = {r["func_name"] for r in direct if r["func_name"]}
        func_label = "  |  ".join(f"def {n}()" for n in sorted(func_names)) if func_names else "unknown scope"

        print(green(f"  Found in: {func_label}"))
        print(green(f"  {len(direct)} hunk(s) in that function  +  {len(callers)} caller hunk(s)"))

        def _review_results(label: str, items: list, revert_patches: list) -> bool:
            if not items:
                return False
            print(f"\n{bold('━' * 72)}")
            print(f"  {bold(cyan(label))}")
            print(bold('━' * 72))

            by_file: dict = {}
            for item in items:
                by_file.setdefault(item["filepath"], []).append(item)

            staged_by_file: dict = {}

            for fp, file_items in by_file.items():
                dt = file_items[0]["diff_text"]
                print(f"\n  {bold(magenta(fp))}  {grey(str(len(file_items)) + ' hunk(s)')}")
                for item in file_items:
                    hunk = item["hunk"]
                    idx = item["hunk_idx"]
                    total = item["total_hunks"]
                    reason = item.get("reason", "")
                    if reason:
                        print(f"  {yellow('↳')} {grey(reason)}")
                    _print_hunk(hunk, idx + 1, total)
                    print()
                    while True:
                        try:
                            ch = input(
                                f"  {cyan('Hunk ' + str(idx+1) + '/' + str(total))}  "
                                f"[{bold('k')}]eep  [{bold('r')}]evert  [{bold('q')}]uit: "
                            ).strip().lower()
                        except KeyboardInterrupt:
                            return True
                        if ch in ("k", ""):
                            print(grey("  → keeping"))
                            break
                        elif ch == "r":
                            staged_by_file.setdefault(fp, (dt, []))
                            staged_by_file[fp][1].append(hunk)
                            print(green("  → marked for revert"))
                            break
                        elif ch == "q":
                            for _fp, (_dt, hs) in staged_by_file.items():
                                if hs:
                                    revert_patches.append(_build_revert_patch(_dt, hs))
                            return True
                        else:
                            print(yellow("  ? Enter k/r/q"))
            for _fp, (_dt, hs) in staged_by_file.items():
                if hs:
                    revert_patches.append(_build_revert_patch(_dt, hs))
                    print(green(f"  ✓  {len(hs)} hunk(s) queued for revert in {_fp}"))
            return False

        revert_patches: list = []

        quit_early = _review_results(
            f"Hunks inside {func_label}", direct, revert_patches
        )
        if quit_early:
            _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)
            return

        if callers:
            caller_funcs = {r["func_name"] for r in callers if r["func_name"]}
            print(f"\n  {cyan('↳')} {bold(str(len(callers)))} hunk(s) in callers of {func_label}")
            try:
                show_c = input("  Review caller hunks? [Y/n]: ").strip().lower()
            except KeyboardInterrupt:
                show_c = "n"
            if show_c != "n":
                quit_early = _review_results(
                    "Caller hunks", callers, revert_patches
                )
                if quit_early:
                    _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)
                    return

        _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)
        return

    if raw.lower() == "all":
        selected_files = changed
    else:
        try:
            idxs = [int(x) - 1 for x in raw.split()]
            selected_files = [changed[i] for i in idxs if 0 <= i < len(changed)]
        except (ValueError, IndexError):
            print(red("  Invalid selection.\n"))
            return

    if not selected_files:
        return

    # Per-file, per-hunk interactive review
    revert_patches = []   # collected patches to apply at the end

    for filepath in selected_files:
        diff_text = _git_diff_file(repo_path, base_sha, head_sha, filepath)
        if not diff_text:
            print(grey(f"\n  (no diff for {filepath})"))
            continue

        hunks = _parse_hunks(diff_text)
        if not hunks:
            continue

        print(f"\n{bold('━' * 72)}")
        print(f"  {bold(magenta('📄  ' + filepath))}  "
              f"{grey(str(len(hunks)) + ' hunk(s)')}")
        print(f"{bold('━' * 72)}")
        print(f"  {grey('[k]eep  [r]evert  [a]ll-revert  [s]kip file  [/search]  [q]uit')}\n")

        selected_hunks = []
        skip_file = False

        for hi, hunk in enumerate(hunks, 1):
            _print_hunk(hunk, hi, len(hunks))
            print()

            while True:
                try:
                    choice = input(
                        f"  {cyan('Hunk ' + str(hi) + '/' + str(len(hunks)))}  "
                        f"[{bold('k')}]eep  [{bold('r')}]evert  "
                        f"[{bold('a')}]ll-revert  [{bold('s')}]kip  "
                        f"[{bold('/')}]search  [{bold('q')}]uit: "
                    ).strip().lower()
                except KeyboardInterrupt:
                    print("\n  Interrupted.\n")
                    return

                if choice in ("k", ""):
                    print(grey("  → keeping"))
                    break
                elif choice == "r":
                    selected_hunks.append(hunk)
                    print(green("  → marked for revert"))
                    break
                elif choice == "a":
                    selected_hunks = hunks[:]
                    print(green(f"  → all {len(hunks)} hunks marked for revert"))
                    skip_file = True
                    break
                elif choice == "s":
                    print(grey("  → skipping file"))
                    skip_file = True
                    selected_hunks = []
                    break
                elif choice.startswith("/"):
                    # inline search within this file
                    query = choice[1:].strip()
                    if not query:
                        try:
                            query = input("  Search query: ").strip()
                        except KeyboardInterrupt:
                            continue
                    jump_hunks, quit_all = _prompt_search_and_jump(
                        hunks, diff_text, filepath, repo_path,
                        revert_patches, base_sha, wf_id
                    )
                    selected_hunks.extend(jump_hunks)
                    if quit_all:
                        if selected_hunks:
                            revert_patches.append(_build_revert_patch(diff_text, selected_hunks))
                        _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)
                        return
                    # after search, continue normal hunk walk
                    break
                elif choice == "q":
                    # Commit what we have so far and exit
                    if selected_hunks:
                        patch = _build_revert_patch(diff_text, selected_hunks)
                        revert_patches.append(patch)
                    _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)
                    return
                else:
                    print(yellow("  ? Enter k/r/a/s//search/q"))

            if skip_file:
                break

        if selected_hunks:
            patch = _build_revert_patch(diff_text, selected_hunks)
            revert_patches.append(patch)
            print(green(f"  ✓  {len(selected_hunks)} hunk(s) queued for revert in {filepath}"))

    _apply_and_commit_regressions(repo_path, revert_patches, base_sha, wf_id)


def _get_gitship_export_path() -> Path:
    """Read export path from gitship config, falling back to ~/gitship_exports."""
    try:
        from gitship.config import load_config
        cfg = load_config()
        p = Path(cfg.get("export_path", "")).expanduser()
        if str(p) and p != Path(""):
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    fallback = Path.home() / "gitship_exports"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\[[0-9;]*m", "", text)


def _export_regression_report(
    repo_path: Path,
    base_sha: str,
    head_sha: str,
    branch: str,
    wf_id: str,
    changed_files: list,
    file_stats: dict,
    all_file_hunks: dict,   # filepath -> (diff_text, list[hunk])  -- may be empty dict
) -> Optional[Path]:
    """
    Write a plain-text regression diff report to the gitship export path.

    Format:
      - Header with repo/workflow/SHA info
      - File summary table (same as terminal view, no colour)
      - Full unified diff for every changed file
      - Footer

    Returns the output Path on success, None on failure.
    """
    export_dir = _get_gitship_export_path()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    repo_name = repo_path.name
    filename = f"regression_{repo_name}_{base_sha[:8]}_{head_sha[:8]}_{ts}.txt"
    out_path = export_dir / filename

    try:
        lines = []
        w = lines.append   # writer shorthand

        w("=" * 72)
        w("GITSHIP REGRESSION DIFF REPORT")
        w("=" * 72)
        w(f"Repo:         {repo_path}")
        w(f"Workflow:     {wf_id}")
        w(f"Branch:       {branch}")
        w(f"Base (pass):  {base_sha}")
        w(f"HEAD:         {head_sha}")
        w(f"Generated:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        w("=" * 72)
        w("")

        # File summary
        w(f"  {'#':<4} {'FILE':<52} {'INS':>6} {'DEL':>6}  {'COMMITS':>7}")
        w("  " + "-" * 72)
        for i, f in enumerate(changed_files, 1):
            st = file_stats.get(f, {})
            ins = st.get("ins", 0)
            del_ = st.get("del", 0)
            commits = st.get("commits", 0)
            w(f"  {str(i):<4} {f:<52} {('+'+str(ins)):>6} {('-'+str(del_)):>6}  {str(commits):>7}")
        w("")
        w("=" * 72)
        w("")

        # Full diffs
        for filepath in changed_files:
            if filepath in all_file_hunks:
                diff_text, hunks = all_file_hunks[filepath]
            else:
                # Fetch on demand if not pre-loaded
                from subprocess import run as sp_run
                r = sp_run(
                    ["git", "diff", "-U10", base_sha, head_sha, "--", filepath],
                    cwd=repo_path, capture_output=True, text=True,
                )
                diff_text = r.stdout if r.returncode == 0 else ""

            if not diff_text:
                w(f"--- {filepath}: (binary or no diff)")
                w("")
                continue

            w(f"{'─' * 72}")
            w(f"FILE: {filepath}")
            w(f"{'─' * 72}")
            w(_strip_ansi(diff_text))
            w("")

        w("=" * 72)
        w("END OF REPORT")
        w("=" * 72)

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    except Exception as e:
        print(red(f"  ✗  Export failed: {e}"))
        return None


def _apply_and_commit_regressions(
    repo_path: Path,
    patches: list[str],
    base_sha: str,
    workflow_id: str,
) -> None:
    """Apply all collected revert patches and optionally commit."""
    if not patches:
        print(grey("\n  No hunks selected for reversion.  Nothing applied.\n"))
        return

    print(f"\n{bold('━' * 72)}")
    print(f"  {cyan('⚙  Applying')} {bold(str(len(patches)))} patch(es)…")

    applied = 0
    for patch in patches:
        if _apply_patch_lines(repo_path, patch):
            applied += 1
        else:
            print(yellow("  ⚠  One patch failed to apply (may conflict). Continuing…"))

    if applied == 0:
        print(red("  ✗  No patches applied successfully.\n"))
        return

    print(green(f"  ✓  {applied}/{len(patches)} patch(es) applied and staged.\n"))

    # Show staged summary
    r = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.stdout.strip():
        print(grey("  Staged changes:"))
        for line in r.stdout.strip().splitlines():
            print(f"    {dim(line)}")
        print()

    # Ask to commit
    try:
        do_commit = input(
            f"  Commit these reverts? [{bold('y')}/N]: "
        ).strip().lower()
    except KeyboardInterrupt:
        print(grey("\n  Changes staged but not committed.  Run `git commit` manually.\n"))
        return

    if do_commit != "y":
        print(grey("  Changes staged.  Run `git commit` when ready.\n"))
        return

    commit_msg = (
        f"fix: revert regression hunks to state at {base_sha[:8]}\n\n"
        f"Workflow: {workflow_id}\n"
        f"Reverted to last-passing commit: {base_sha[:8]}\n"
        f"Tool: gitship ci regression-diff"
    )

    r = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode == 0:
        new_sha = ""
        sha_r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path, capture_output=True, text=True,
        )
        new_sha = sha_r.stdout.strip()
        print(green(f"  ✓  Committed as {bold(new_sha)}\n"))

        try:
            do_push = input(f"  Push to origin/{_local_branch(repo_path)}? [y/N]: ").strip().lower()
        except KeyboardInterrupt:
            do_push = "n"

        if do_push == "y":
            branch = _local_branch(repo_path)
            p = subprocess.run(
                ["git", "push", "origin", f"HEAD:{branch}"],
                cwd=repo_path, capture_output=True, text=True,
            )
            if p.returncode == 0:
                print(green(f"  ✓  Pushed to origin/{branch}\n"))
            else:
                print(red(f"  ✗  Push failed: {p.stderr.strip()}\n"))
    else:
        print(red(f"  ✗  Commit failed: {r.stderr.strip()}\n"))
        print(grey("  Staged changes preserved. Resolve conflicts then `git commit`.\n"))


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
        print(f"\n  {bold('DIAGNOSE')}")
        print(f"    {bold('13')}. {bold(red('Regression diff'))}  Last passing run → HEAD  (hunk-level revert)")
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
        elif choice == "13":
            show_regression_diff(repo_path)
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
    regression_diff: bool = False,
) -> None:
    """Non-interactive CLI entrypoint."""
    if regression_diff:
        show_regression_diff(repo_path)
    elif overview:
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