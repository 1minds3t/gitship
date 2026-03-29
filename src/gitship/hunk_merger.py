#!/usr/bin/env python3
"""
hunk_merger.py — Interactive hunk-by-hunk branch merger.

Features:
  - Legend always shown at bottom (after hunk, not top)
  - Syntax check (py_compile) after every t/e apply — auto-reverts on fail
  - Undo last applied hunk with [u] — byte-exact file snapshot before each apply
  - Impact analysis with [i] — AST-based: shows what functions this hunk touches,
    who calls them, and which OTHER hunks in this session touch the same symbols
  - State saved after every decision — quit and resume safely at any time

Keys:
  t  take theirs      o  keep ours      e  edit in $EDITOR
  s  skip+annotate    u  undo last       i  impact analysis
  ?  full context     b  both sides     l  skip log    q  quit+save
"""

import argparse
import ast
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── ANSI colours ──────────────────────────────────────────────────────────────

R       = '\033[0m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
RED     = '\033[31m'
GREEN   = '\033[32m'
YELLOW  = '\033[33m'
CYAN    = '\033[36m'
MAGENTA = '\033[35m'
BRED    = '\033[91m'
BGREEN  = '\033[92m'
BYELLOW = '\033[93m'
BCYAN   = '\033[96m'
BMAGENTA= '\033[95m'

def c_add(s):  return f"{GREEN}{s}{R}"
def c_del(s):  return f"{RED}{s}{R}"
def c_hdr(s):  return f"{CYAN}{s}{R}"
def c_dim(s):  return f"{DIM}{s}{R}"
def c_warn(s): return f"{YELLOW}{s}{R}"
def c_ok(s):   return f"{BGREEN}{s}{R}"
def c_bad(s):  return f"{RED}{s}{R}"

# ── Debug flag ─────────────────────────────────────────────────────────────────
# Set True to emit per-pass diagnostics explaining why hunks are/aren't grouped.
# Shows: unmatched hunk counts, hub-suppressed callees, dropped IPC keys,
# borderline rename candidates, cluster fqname mismatches, final ungrouped tally.
# Flip to False once grouping is working to your satisfaction.
DEBUG_GROUPS: bool = True

LEGEND = (
    f"\n  {DIM}Keys:{R}  "
    f"[{BGREEN}t{R}] take theirs  "
    f"[{BYELLOW}o{R}] keep ours  "
    f"[{BCYAN}e{R}] edit  "
    f"[{MAGENTA}s{R}] skip  "
    f"[{BRED}u{R}] undo  "
    f"[{CYAN}i{R}] impact  "
    f"[{BYELLOW}m{R}] move-to-group  "
    f"[?] context  [b] both  [l] log  "
    f"[{BMAGENTA}c{R}] checkpoint  [q] quit"
)


# ── Data structures ────────────────────────────────────────────────────────────

class Hunk:
    __slots__ = ("header", "lines")

    def __init__(self, header: str, lines: List[str]):
        self.header = header
        self.lines  = lines

    @property
    def adds(self):
        return sum(1 for l in self.lines if l.startswith('+') and not l.startswith('+++'))

    @property
    def dels(self):
        return sum(1 for l in self.lines if l.startswith('-') and not l.startswith('---'))

    @property
    def stat(self):
        return f"+{self.adds} -{self.dels}"

    def target_line_range(self) -> Optional[Tuple[int, int]]:
        m = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', self.header)
        if not m:
            return None
        start = int(m.group(3))
        count = int(m.group(4)) if m.group(4) else 1
        return (start, start + count - 1)

    def source_line_range(self) -> Optional[Tuple[int, int]]:
        m = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', self.header)
        if not m:
            return None
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) else 1
        return (start, start + count - 1)


class FileDiff:
    __slots__ = ("path", "hunks")

    def __init__(self, path: str, hunks: List[Hunk]):
        self.path  = path
        self.hunks = hunks


# ── State persistence ──────────────────────────────────────────────────────────

import hashlib as _hashlib

HUNK_MERGE_DIR = ".gitship/hunk-merge"


def _session_dir(repo: Path, source: str, target: str) -> Path:
    """
    Return (and create) the session directory for this source->target pair.
    Path: <repo>/.gitship/hunk-merge/<sha8>/
    sha8 = first 8 hex chars of sha256(source + "__" + target).
    Branch names are stored in state.json and at the top of condensed.diff.
    """
    key  = f"{source}__{target}"
    sha8 = _hashlib.sha256(key.encode()).hexdigest()[:8]
    d    = repo / HUNK_MERGE_DIR / sha8
    d.mkdir(parents=True, exist_ok=True)
    gi = repo / ".gitignore"
    if gi.exists():
        txt = gi.read_text()
        if ".gitship/" not in txt:
            gi.write_text(txt.rstrip() + "\n.gitship/\n")
    return d


def _state_path(repo: Path, source: str, target: str) -> Path:
    return _session_dir(repo, source, target) / "state.json"


def _diff_path(repo: Path, source: str, target: str) -> Path:
    return _session_dir(repo, source, target) / "condensed.diff"


def _load_state(repo: Path, source: str = "", target: str = "") -> Dict:
    if source and target:
        p = _state_path(repo, source, target)
    else:
        base = repo / HUNK_MERGE_DIR
        if not base.exists():
            return {"decisions": [], "meta": {}}
        candidates = sorted(base.glob("*/state.json"),
                            key=lambda f: f.stat().st_mtime, reverse=True)
        p = candidates[0] if candidates else None
        if p is None:
            return {"decisions": [], "meta": {}}
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"decisions": [], "meta": {}}


def _save_state(repo: Path, state: Dict, source: str = "", target: str = ""):
    if not source or not target:
        meta   = state.get("meta", {})
        source = meta.get("source", source)
        target = meta.get("target", target)
    if source and target:
        p = _state_path(repo, source, target)
        p.write_text(json.dumps(state, indent=2))
        n = len(state.get("decisions", []))
        print(f"  {chr(27)}[2m[state] saved {n} decision(s) → {p.name}{chr(27)}[0m", flush=True)
    else:
        print(f"  {chr(27)}[33m[state] WARNING: no source/target — decision NOT saved! meta={state.get(chr(39)+chr(109)+chr(101)+chr(116)+chr(97)+chr(39),{})}{chr(27)}[0m", flush=True)


def _key(file: str, idx: int) -> str:
    return f"{file}::{idx}"


def _get_decision(state: Dict, file: str, idx: int) -> Optional[Dict]:
    k = _key(file, idx)
    for d in state["decisions"]:
        if d.get("key") == k:
            return d
    return None


def _upsert_decision(state: Dict, file: str, idx: int, action: str,
                     annotation: str, hunk_header: str):
    k = _key(file, idx)
    entry = {
        "key": k, "file": file, "hunk_index": idx,
        "hunk_header": hunk_header, "action": action,
        "annotation": annotation,
        "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for i, d in enumerate(state["decisions"]):
        if d.get("key") == k:
            state["decisions"][i] = entry
            return
    state["decisions"].append(entry)


def validate_state(repo: Path, state: Dict, files: List["FileDiff"]) -> Dict:
    """
    Cross-check saved decisions against the live diff.

    For every decision in state, verify its hunk_header still matches the
    hunk at the same (file, index) position in the current diff.  Mismatches
    mean the state was hand-edited or the diff changed underneath us.

    Returns a report dict:
      {
        "ok": bool,
        "stale": [ {key, file, hunk_index, saved_header, live_header}, … ],
        "orphan": [ {key, file, hunk_index, action}, … ],   # decisions with no live hunk
        "total_decisions": int,
        "total_live_hunks": int,
      }
    """
    # Build a fast lookup: (file, idx) → live hunk header
    live: Dict[str, str] = {}
    total_live = 0
    for fd in files:
        for i, hunk in enumerate(fd.hunks):
            live[_key(fd.path, i)] = hunk.header.strip()
            total_live += 1

    stale: List[Dict] = []
    orphan: List[Dict] = []

    for d in state.get("decisions", []):
        k = d.get("key", "")
        saved_hdr = (d.get("hunk_header") or "").strip()
        if k not in live:
            orphan.append({
                "key": k,
                "file": d.get("file", "?"),
                "hunk_index": d.get("hunk_index", -1),
                "action": d.get("action", "?"),
            })
        elif saved_hdr and live[k] != saved_hdr:
            stale.append({
                "key": k,
                "file": d.get("file", "?"),
                "hunk_index": d.get("hunk_index", -1),
                "saved_header": saved_hdr,
                "live_header":  live[k],
            })

    ok = not stale and not orphan
    return {
        "ok": ok,
        "stale": stale,
        "orphan": orphan,
        "total_decisions": len(state.get("decisions", [])),
        "total_live_hunks": total_live,
    }


def print_validation_report(report: Dict):
    """Pretty-print a validate_state() report."""
    total_d = report["total_decisions"]
    total_h = report["total_live_hunks"]

    if report["ok"]:
        print(c_ok(f"  ✓ State valid — {total_d} decisions match {total_h} live hunks"))
        return

    print(f"\n{BOLD}{BRED}  ⚠  STATE MISMATCH DETECTED{R}")
    print(f"  {total_d} saved decisions vs {total_h} live hunks in current diff.\n")

    if report["stale"]:
        print(f"  {BOLD}{YELLOW}Stale headers{R} "
              f"{c_dim('(hunk exists but header changed — diff may have shifted):')}")
        for s in report["stale"]:
            print(f"    {CYAN}{s['file']}{R}  hunk #{s['hunk_index']+1}")
            print(f"      saved : {c_dim(s['saved_header'][:70])}")
            print(f"      live  : {c_warn(s['live_header'][:70])}")

    if report["orphan"]:
        print(f"\n  {BOLD}{RED}Orphan decisions{R} "
              f"{c_dim('(no matching hunk in current diff — may be already applied):')}")
        for o in report["orphan"]:
            col = {"theirs": BGREEN, "ours": BYELLOW,
                   "edited": BCYAN, "skip": MAGENTA}.get(o["action"], DIM)
            print(f"    {col}[{o['action'].upper()}]{R}  "
                  f"{CYAN}{o['file']}{R}  hunk #{o['hunk_index']+1}")

    print(f"\n  {c_dim('Options:  reset state (--reset)  or  start fresh (n) in the merger menu')}")


# ── Diff ──────────────────────────────────────────────────────────────────────

def _get_diff(repo: Path, source: str, target: str,
              file_filter: Optional[str] = None) -> str:
    """
    Generate condensed diff, write to .gitship/hunk-merge/<hash>/condensed.diff
    with a human-readable header, and return the diff text.
    Always regenerated fresh — stale diffs must never confuse grouping.
    """
    cmd = [
        "git", "diff", f"{target}..{source}",
        "-U1", "-w", "--ignore-blank-lines", "--minimal", "--patience",
    ]
    if file_filter:
        cmd += ["--", file_filter]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    diff_text = r.stdout
    try:
        dp = _diff_path(repo, source, target)
        header = (
            f"# hunk-merge session: {source} -> {target}\n"
            f"# generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# filter: {file_filter or 'none'}\n#\n"
        )
        dp.write_text(header + diff_text)
    except Exception:
        pass
    return diff_text


def parse_diff(diff_text: str) -> List[FileDiff]:
    files: List[FileDiff] = []
    cur_path: Optional[str] = None
    cur_hunks: List[Hunk] = []
    cur_hunk: Optional[Hunk] = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if cur_path is not None:
                if cur_hunk:
                    cur_hunks.append(cur_hunk)
                    cur_hunk = None
                if cur_hunks:
                    files.append(FileDiff(cur_path, cur_hunks))
            cur_path = None
            cur_hunks = []
        elif line.startswith("+++ b/"):
            cur_path = line[6:]
        elif line.startswith("@@ "):
            if cur_hunk:
                cur_hunks.append(cur_hunk)
            cur_hunk = Hunk(line, [])
        elif cur_hunk is not None and not line.startswith(("--- ", "+++ ", "index ", "new file")):
            cur_hunk.lines.append(line)

    if cur_path is not None:
        if cur_hunk:
            cur_hunks.append(cur_hunk)
        if cur_hunks:
            files.append(FileDiff(cur_path, cur_hunks))
    return files


# ── Display ───────────────────────────────────────────────────────────────────

def _print_hunk(hunk: Hunk, limit: int = 999):
    ctx = 0
    for line in hunk.lines:
        if line.startswith('+') and not line.startswith('+++'):
            print("    " + c_add(line))
        elif line.startswith('-') and not line.startswith('---'):
            print("    " + c_del(line))
        elif line.startswith('@@'):
            print("    " + c_hdr(line))
        else:
            if limit < 999 and ctx >= limit:
                continue
            print("    " + c_dim(line))
            ctx += 1
    if len(hunk.lines) > 60:
        print(c_dim(f"    ... ({len(hunk.lines)} lines total)"))


def _find_hunk_in_file(file_content: str, hunk: Hunk) -> Optional[int]:
    """
    Find where this hunk actually sits in file_content by context-matching.
    Returns 0-based line index of the first context/deleted line, or None.
    Uses the same needle-search logic as _fuzzy_apply but just for display.
    """
    m = re.match(r'@@ -(\d+),?(\d*) ', hunk.header)
    hint = int(m.group(1)) - 1 if m else 0

    lines  = file_content.splitlines()
    norm   = [l.rstrip() for l in lines]
    needle = []
    for line in hunk.lines:
        if line.startswith('+') and not line.startswith('+++'):
            continue  # additions don't exist in our file yet
        needle.append((line[1:] if line and line[0] in (' ', '-') else line).rstrip())

    if not needle:
        return hint

    n = len(needle)
    # Try hint position first (±30 lines), then full scan
    search_order = list(range(max(0, hint - 30), min(len(norm), hint + 30)))
    search_order += [i for i in range(len(norm)) if i not in set(search_order)]

    for start in search_order:
        if start + n > len(norm):
            continue
        if norm[start:start + n] == needle:
            return start

    # Fuzzy: find the longest matching subsequence starting anywhere
    best, best_start = 0, hint
    for start in range(len(norm)):
        matched = sum(1 for a, b in zip(needle, norm[start:]) if a == b)
        if matched > best:
            best, best_start = matched, start
    return best_start if best >= max(1, n // 2) else hint


def _show_context(repo: Path, fd_path: str, hunk: Hunk,
                  ours_content: str, pad: int = 8):
    """
    Print the hunk diff embedded in its real file context (pad lines above/below).
    Pulls from the live working-tree file (ours_content) so line numbers are current.
    """
    file_lines = ours_content.splitlines() if ours_content else []
    anchor = _find_hunk_in_file(ours_content, hunk) if ours_content else 0

    # Count how many lines the hunk covers in our file (context + deletions)
    hunk_span = sum(1 for l in hunk.lines
                    if not (l.startswith('+') and not l.startswith('+++')))

    pre_start  = max(0, anchor - pad)
    post_end   = min(len(file_lines), anchor + hunk_span + pad)

    print(f"\n{BOLD}{'─'*20} CONTEXT  {fd_path} {'─'*20}{R}")
    print(c_hdr(f"    {hunk.header.strip()}"))
    print()

    # Lines before the hunk
    for i in range(pre_start, anchor):
        print(f"    {c_dim(str(i+1).rjust(5))}  {c_dim(file_lines[i])}")

    # The hunk itself, colourised
    out_lineno = anchor  # tracks position in OUR file for display
    for line in hunk.lines:
        if line.startswith('+') and not line.startswith('+++'):
            print(f"    {'     '}  {c_add(line)}")
        elif line.startswith('-') and not line.startswith('---'):
            lno = str(out_lineno + 1).rjust(5)
            print(f"    {RED}{lno}{R}  {c_del(line)}")
            out_lineno += 1
        elif line.startswith('@@'):
            print(f"    {c_hdr(line)}")
        else:
            lno = str(out_lineno + 1).rjust(5)
            print(f"    {c_dim(lno)}  {c_dim(line)}")
            out_lineno += 1

    # Lines after the hunk
    for i in range(anchor + hunk_span, post_end):
        print(f"    {c_dim(str(i+1).rjust(5))}  {c_dim(file_lines[i])}")

    print(f"{BOLD}{'─'*61}{R}")


def _manual_combine(repo: Path, abs_path: Path, fd_path: str,
                    ours_content: str, hunk: Hunk, start_with: str, pad: int = 6):
    """
    Open ONLY the hunk region (± pad lines) in the editor with the other side
    as a commented reference. Splices result back into the full file.
    Returns True if applied, False otherwise.
    """
    full_text  = ours_content or abs_path.read_text(encoding="utf-8", errors="replace")
    file_lines = full_text.splitlines(keepends=True)
    parsed     = _parse_hunk_lines(hunk)

    anchor    = _find_hunk_in_file(full_text, hunk)
    hunk_span = sum(1 for l in hunk.lines
                    if not (l.startswith('+') and not l.startswith('+++')))
    pre_start = max(0, anchor - pad)
    post_end  = min(len(file_lines), anchor + hunk_span + pad)

    sep = "=" * 72

    if start_with == "ours":
        region_text = "".join(file_lines[pre_start:post_end])
        ref_lines   = [t for k, t in parsed if k == "+"]
        ref_label   = "THEIRS — incoming additions (copy what you want into the region above)"
    else:
        region_only = "".join(file_lines[pre_start:post_end])
        theirs_ver  = _apply_hunk_to_text(region_only, hunk, reverse=False)
        region_text = theirs_ver if theirs_ver != region_only else region_only
        ref_lines   = [l.rstrip("\n") for l in file_lines[pre_start:post_end]]
        ref_label   = "OURS — current file (copy what you want into the region above)"

    is_py = fd_path.endswith(".py")
    if is_py:
        ref_block = (
            f"\n# {sep}\n"
            f"# MANUAL COMBINE REFERENCE  [{fd_path}  lines {pre_start+1}–{post_end}]\n"
            f"# {ref_label}\n"
            f"# Edit the region ABOVE this block. Delete this entire block when done.\n"
            f"# {sep}\n"
            + "".join(f"# {l}\n" for l in ref_lines)
            + f"# {sep}\n"
        )
        marker = f"# {sep}"
    else:
        ref_block = (
            f"\n/* {sep}\n"
            f" * MANUAL COMBINE REFERENCE  [{fd_path}  lines {pre_start+1}–{post_end}]\n"
            f" * {ref_label}\n"
            f" * Edit the region ABOVE this block. Delete this entire block when done.\n"
            f" * {sep}\n"
            + "".join(f" * {l}\n" for l in ref_lines)
            + f" * {sep} */\n"
        )
        marker = f"/* {sep}"

    edit_text = region_text.rstrip("\n") + "\n" + ref_block

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=Path(fd_path).suffix or ".txt",
        delete=False, encoding="utf-8"
    ) as tf:
        tf.write(edit_text)
        tmp = tf.name

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    subprocess.run([editor, "+1", tmp])
    result_text = Path(tmp).read_text(encoding="utf-8", errors="replace")
    Path(tmp).unlink(missing_ok=True)

    if marker in result_text:
        result_text = result_text[:result_text.index(marker)].rstrip("\n") + "\n"

    if "<<<<<<< " in result_text:
        print(f"  {YELLOW}⚠  Conflict markers still present — not applying.{R}")
        return False

    before = "".join(file_lines[:pre_start])
    after  = "".join(file_lines[post_end:])
    if result_text and not result_text.endswith("\n"):
        result_text += "\n"
    abs_path.write_text(before + result_text + after, encoding="utf-8")
    subprocess.run(["git", "add", fd_path], cwd=repo, capture_output=True)
    print(f"  {BGREEN}✓ Manual combine applied and staged.{R}")
    return True


def _show_both(repo: Path, ours_content: str, hunk: Hunk, fd_path: str, pad: int = 6):
    """
    Combine both sides of a hunk. Flow:

      1. Try git 3-way merge automatically.
         Clean result  → show it → [y] accept / [e] edit / [n] don't accept
         Conflicts     → show markers → go to manual combine

      2. Manual combine fallback (always available via [n] or on conflict):
         User picks:  [o] start with ours  /  [t] start with theirs
         Editor opens with chosen version + other side as commented reference.
         User manually merges, saves, done.
    """
    import shutil

    abs_path = repo / fd_path
    if not abs_path.exists():
        print(f"\n{BOLD}{'─'*22} THEIRS (incoming diff) {'─'*22}{R}")
        _print_hunk(hunk)
        return False

    # ── Always show both sides first so user knows what they're combining ─────
    print(f"\n{BOLD}{'─'*20} THEIRS — incoming change {'─'*20}{R}")
    _print_hunk(hunk, limit=999)

    file_lines = (ours_content or "").splitlines()
    anchor    = _find_hunk_in_file(ours_content or "", hunk)
    hunk_span = sum(1 for l in hunk.lines
                    if not (l.startswith('+') and not l.startswith('+++')))
    pre_start = max(0, anchor - pad)
    post_end  = min(len(file_lines), anchor + hunk_span + pad)
    print(f"\n{BOLD}{'─'*20} OURS — current file lines {pre_start+1}–{post_end} {'─'*20}{R}")
    for i in range(pre_start, post_end):
        marker = f"{YELLOW}►{R}" if anchor <= i < anchor + hunk_span else " "
        print(f"  {marker} {c_dim(str(i+1).rjust(5))}  {file_lines[i]}")
    print(f"{BOLD}{'─'*64}{R}")

    # ── Step 0: already-applied detection ────────────────────────────────────
    _base_check = _apply_hunk_to_text(ours_content or "", hunk, reverse=True)
    if _base_check != (ours_content or ""):
        parsed_check = _parse_hunk_lines(hunk)
        add_lines    = [t.rstrip() for k, t in parsed_check if k == "+"]
        ours_set     = set(l.rstrip() for l in (ours_content or "").splitlines())
        already_n    = sum(1 for l in add_lines if l in ours_set)
        print(f"\n{BGREEN}  ✓ Hunk appears already applied{R} "
              f"{c_dim(f'({already_n}/{len(add_lines)} added lines already present).')}")
        print(f"  y  Record as done and advance  (default)")
        print(f"  n  Ignore — proceed with merge anyway")
        if _safe_input(f"  {BOLD}>{R} ").strip().lower() not in ("n", "no"):
            subprocess.run(["git", "add", fd_path], cwd=repo, capture_output=True)
            print(f"  {BGREEN}✓ Staged as-is and recorded.{R}")
            return True

    # ── Step 1: attempt 3-way auto-merge ─────────────────────────────────────
    auto_merged = None
    has_conflicts = False

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ours_file = td / "ours"
        shutil.copy2(abs_path, ours_file)

        base_content   = _apply_hunk_to_text(ours_content or "", hunk, reverse=True)
        theirs_content = _apply_hunk_to_text(base_content, hunk, reverse=False)
        (td / "base").write_text(base_content,   encoding="utf-8", errors="replace")
        (td / "theirs").write_text(theirs_content, encoding="utf-8", errors="replace")

        mr = subprocess.run(
            ["git", "merge-file", "--marker-size=7",
             str(ours_file), str(td / "base"), str(td / "theirs")],
            capture_output=True, text=True
        )
        if mr.returncode >= 0:
            auto_merged   = ours_file.read_text(encoding="utf-8", errors="replace")
            has_conflicts = mr.returncode > 0

    if auto_merged and not has_conflicts:
        # ── Clean auto-merge ──────────────────────────────────────────────────
        print(f"\n{BGREEN}  ✓ 3-way auto-merge succeeded — no conflicts.{R}")
        print(f"{DIM}{'─'*64}{R}")
        merged_lines = auto_merged.splitlines()
        show_start = max(0, anchor - pad)
        show_end   = min(len(merged_lines), anchor + hunk_span + pad + 10)
        for ln in merged_lines[show_start:show_end]:
            print(f"  {ln}")
        print(f"{DIM}{'─'*64}{R}")
        print(f"  {BOLD}Options:{R}")
        print(f"  y  Accept auto-merge result")
        print(f"  e  Edit auto-merge result before accepting")
        print(f"  n  Discard — go to manual combine instead")
        ans = _safe_input(f"  {BOLD}>{R} ").strip().lower()

        if ans in ("y", "yes"):
            abs_path.write_text(auto_merged, encoding="utf-8")
            subprocess.run(["git", "add", fd_path], cwd=repo, capture_output=True)
            print(f"  {BGREEN}✓ Auto-merge applied and staged.{R}")
            return True
        elif ans in ("e", "edit"):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=Path(fd_path).suffix or ".txt",
                delete=False, encoding="utf-8"
            ) as tf:
                tf.write(auto_merged); tmp = tf.name
            editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
            subprocess.run([editor, tmp])
            edited = Path(tmp).read_text(encoding="utf-8", errors="replace")
            Path(tmp).unlink(missing_ok=True)
            if "<<<<<<< " not in edited:
                abs_path.write_text(edited, encoding="utf-8")
                subprocess.run(["git", "add", fd_path], cwd=repo, capture_output=True)
                print(f"  {BGREEN}✓ Edited merge applied and staged.{R}")
                return True
            else:
                print(f"  {YELLOW}⚠  Conflict markers still present — not applying.{R}")
                return False
        # ans == "n" → fall through to manual combine
        return False

    elif auto_merged and has_conflicts:
        conflict_count = auto_merged.count("<<<<<<< ")
        print(f"\n{YELLOW}  ⚠  Auto-merge found {conflict_count} conflict(s) — needs manual resolution.{R}")
        # fall through to manual combine
    else:
        print(f"\n{YELLOW}  git merge-file unavailable — going to manual combine.{R}")

    # ── Step 2: manual combine ────────────────────────────────────────────────
    print()
    print(f"  {BOLD}Manual combine — start with:{R}")
    print(f"  o  Start with OURS   (keep our version, add what you need from theirs)")
    print(f"  t  Start with THEIRS (take their version, add what you need from ours)")
    print(f"  x  Cancel — do nothing")
    print()
    choice = _safe_input(f"  {BOLD}>{R} ").strip().lower()

    if choice == "o":
        return _manual_combine(repo, abs_path, fd_path, ours_content or "", hunk, "ours", pad)
    elif choice == "t":
        return _manual_combine(repo, abs_path, fd_path, ours_content or "", hunk, "theirs", pad)
    else:
        print(f"  {DIM}Cancelled — file unchanged.{R}")
        return False


def _apply_hunk_to_text(text: str, hunk: Hunk, reverse: bool = False) -> str:
    """
    Apply (or reverse-apply) a hunk to a text string.
    Used to reconstruct BASE and THEIRS for the 3-way merge.
    Returns the modified text, or the original if the hunk can't be located.
    """
    lines = text.splitlines(keepends=True)
    parsed = _parse_hunk_lines(hunk)

    # Determine which lines are context (both sides) vs add/remove
    if not reverse:
        context_and_remove = [t for k, t in parsed if k in (" ", "-")]
        context_and_add    = [t for k, t in parsed if k in (" ", "+")]
        remove_lines = [t for k, t in parsed if k == "-"]
    else:
        # Swap: what was "added" is now what we remove, and vice versa
        context_and_remove = [t for k, t in parsed if k in (" ", "+")]
        context_and_add    = [t for k, t in parsed if k in (" ", "-")]
        remove_lines = [t for k, t in parsed if k == "+"]

    # Find the anchor in the file using context lines
    context_lines = [t for k, t in parsed if k == " "]
    anchor = -1
    if context_lines:
        needle = context_lines[0].rstrip("\n")
        for i, line in enumerate(lines):
            if line.rstrip("\n") == needle:
                anchor = i
                break
    if anchor == -1:
        return text  # Can't find anchor — return unchanged

    # Replace the region
    result = lines[:anchor]
    result += [l if l.endswith("\n") else l + "\n" for l in context_and_add]
    # Skip past the lines we're replacing
    skip = len(context_and_remove)
    result += lines[anchor + skip:]
    return "".join(result)


# ── Syntax check ──────────────────────────────────────────────────────────────

def _syntax_check(repo: Path, file_path: str) -> Tuple[bool, str]:
    if not file_path.endswith(".py"):
        return True, ""
    full = repo / file_path
    if not full.exists():
        return True, ""
    try:
        py_compile.compile(str(full), doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)


# ── Snapshot / revert ─────────────────────────────────────────────────────────

def _snapshot(repo: Path, file_path: str) -> Optional[bytes]:
    p = repo / file_path
    return p.read_bytes() if p.exists() else None


def _restore(repo: Path, file_path: str, snap: Optional[bytes]):
    p = repo / file_path
    if snap is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(snap)
    subprocess.run(["git", "checkout", "HEAD", "--", file_path],
                   cwd=repo, capture_output=True)


# ── Apply hunk ────────────────────────────────────────────────────────────────

def _parse_hunk_lines(hunk: Hunk):
    """Split hunk lines into (kind, text) where kind is '+'/'-'/' '."""
    result = []
    for line in hunk.lines:
        if line.startswith('+') and not line.startswith('+++'):
            result.append(('+', line[1:]))
        elif line.startswith('-') and not line.startswith('---'):
            result.append(('-', line[1:]))
        else:
            result.append((' ', line[1:] if line.startswith(' ') else line))
    return result


def _fuzzy_apply(file_path: Path, hunk: Hunk, fuzz: int = 3) -> Tuple[bool, str]:
    """
    Context-matching apply — ignores line numbers entirely.

    Algorithm:
      1. Extract context lines (space-prefix) and deleted lines from the hunk.
      2. Build a "needle" = the full before-block (context + deletions in order).
      3. Search the file for the best-matching window, allowing up to `fuzz`
         leading/trailing context lines to be missing (imitates patch -F).
      4. At the match site: remove deleted lines, insert added lines, write back.

    Returns (ok, message).
    """
    if not file_path.exists():
        return False, f"file not found: {file_path}"

    try:
        original = file_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return False, str(e)

    parsed   = _parse_hunk_lines(hunk)
    # The "before" view: context + deleted lines (what we expect to find)
    before   = [(k, t) for k, t in parsed if k in (' ', '-')]
    # What we'll insert: context + added lines (what we write)
    after    = [(k, t) for k, t in parsed if k in (' ', '+')]

    if not before:
        return False, "hunk has no context or deletions to locate"

    file_lines = original.splitlines(keepends=True)
    # Normalise for matching: strip trailing whitespace
    norm = [l.rstrip('\n\r').rstrip() for l in file_lines]

    needle_text = [t.rstrip() for _, t in before]
    n = len(needle_text)

    best_pos  = -1
    best_score = -1

    # Slide window over file, allowing up to `fuzz` missing lines at either end
    for start in range(len(norm)):
        for trim_head in range(min(fuzz + 1, n)):
            sub_needle = needle_text[trim_head:]
            if not sub_needle:
                continue
            end = start + len(sub_needle)
            if end > len(norm):
                break
            window = norm[start:end]
            matches = sum(1 for a, b in zip(sub_needle, window) if a == b)
            score   = matches - trim_head * 0.5   # penalise skipped context
            if matches == len(sub_needle) and score > best_score:
                best_score = score
                best_pos   = start
                best_trim  = trim_head
                break         # exact match for this start — no need to try more trims
        if best_score == len(needle_text):
            break             # perfect match found early

    if best_pos == -1:
        # Last-ditch: find just the deleted lines ignoring context entirely
        del_only = [t.rstrip() for k, t in before if k == '-']
        if del_only:
            for start in range(len(norm)):
                end = start + len(del_only)
                if end > len(norm):
                    break
                if [l for l in norm[start:end]] == del_only:
                    best_pos   = start
                    best_trim  = len([k for k, _ in before if k == ' '])
                    break

    if best_pos == -1:
        return False, (
            "fuzzy match failed — context not found in file.\n"
            "  Try [e]dit to manually adjust the hunk, or [b] to see both sides."
        )

    # Reconstruct: lines before the match + after-block + lines after the match
    match_len   = len(needle_text) - best_trim
    after_lines = [t for k, t in after[best_trim:]]   # skip trimmed head context

    # Preserve original line endings from file
    def _eol(idx):
        if idx < len(file_lines):
            raw = file_lines[idx]
            if raw.endswith('\r\n'): return '\r\n'
            if raw.endswith('\r'):   return '\r'
        return '\n'

    eol = _eol(best_pos)
    new_lines = (
        file_lines[:best_pos]
        + [l.rstrip('\n\r') + eol for l in after_lines]
        + file_lines[best_pos + match_len:]
    )

    try:
        file_path.write_text(''.join(new_lines), encoding='utf-8')
    except Exception as e:
        return False, f"write failed: {e}"

    return True, f"fuzzy applied at line {best_pos + 1} (fuzz={best_trim})"


def _apply_hunk(repo: Path, file_path: str, hunk: Hunk) -> Tuple[bool, str]:
    """
    Try git apply first (exact, index-aware).
    On failure fall back to fuzzy context-matching apply.
    On fuzzy success, stage the file so git index stays consistent.
    """
    full = repo / file_path
    patch = "\n".join([
        f"--- a/{file_path}",
        f"+++ b/{file_path}",
        hunk.header,
    ] + hunk.lines) + "\n"

    # ── Attempt 1: git apply (exact) ─────────────────────────────────────────
    r = subprocess.run(
        ["git", "apply", "--index", "--whitespace=fix", "-"],
        input=patch, cwd=repo, capture_output=True, text=True,
    )
    if r.returncode == 0:
        return True, "git apply (exact)"

    git_err = (r.stderr or r.stdout).strip()

    # ── Attempt 2: git apply with --recount (handles offset drift) ───────────
    r2 = subprocess.run(
        ["git", "apply", "--index", "--whitespace=fix", "--recount", "-"],
        input=patch, cwd=repo, capture_output=True, text=True,
    )
    if r2.returncode == 0:
        return True, "git apply --recount"

    # ── Attempt 3: fuzzy context-matching ────────────────────────────────────
    print(f"  {DIM}git apply failed — trying fuzzy context match...{R}")
    ok, msg = _fuzzy_apply(full, hunk)
    if ok:
        # Stage the modified file so git index stays in sync
        subprocess.run(["git", "add", file_path], cwd=repo, capture_output=True)
        return True, f"fuzzy: {msg}"

    # All attempts failed — report git's error (most useful) + fuzzy reason
    return False, f"{git_err}\n  fuzzy: {msg}"


# ── Editor ────────────────────────────────────────────────────────────────────

def _open_editor(hunk: Hunk, ours_content: str, file_path: str) -> Optional[List[str]]:
    header = [
        "# ── EDIT MODE ─────────────────────────────────────────────────────",
        f"# File: {file_path}",
        f"# Hunk: {hunk.header.strip()}",
        "# Edit the diff lines below. + = add, - = remove, space = context.",
        "# Save and quit to apply. Clear all content to cancel.",
        "# ───────────────────────────────────────────────────────────────────",
        "",
    ]
    m = re.match(r'@@ -(\d+),?(\d*) ', hunk.header)
    if m and ours_content:
        start = int(m.group(1)) - 1
        count = int(m.group(2) or "1")
        region = ours_content.splitlines()[start: start + count + 4]
        header += ["# ── CURRENT TARGET (reference) ──"]
        header += [f"# {str(start+i+1).rjust(4)}  {l}" for i, l in enumerate(region)]
        header += ["", "# ── HUNK TO EDIT ──"]

    content = "\n".join(header + hunk.lines) + "\n"
    editor  = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    with tempfile.NamedTemporaryFile(suffix=".patch", mode="w",
                                     delete=False, prefix="hunk_edit_") as f:
        f.write(content)
        tmp = f.name
    try:
        subprocess.run([editor, tmp])
        result = [l for l in Path(tmp).read_text().splitlines()
                  if not l.startswith("#")]
        while result and not result[0].strip():  result.pop(0)
        while result and not result[-1].strip(): result.pop()
        return result if result else None
    finally:
        Path(tmp).unlink(missing_ok=True)


# ── AST impact analysis ───────────────────────────────────────────────────────

_SKIP_BUILTINS = frozenset({
    # Python builtins
    'if', 'for', 'while', 'with', 'print', 'len', 'range', 'str', 'int',
    'list', 'dict', 'set', 'tuple', 'bool', 'super', 'type', 'isinstance',
    'hasattr', 'getattr', 'setattr', 'return', 'yield', 'raise', 'import',
    'open', 'zip', 'map', 'filter', 'sorted', 'enumerate', 'repr', 'any', 'all',
    'next', 'iter', 'sum', 'min', 'max', 'abs', 'round', 'id', 'hash',
    'vars', 'dir', 'callable', 'staticmethod', 'classmethod', 'property',
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'AttributeError',
    'RuntimeError', 'OSError', 'IOError', 'StopIteration', 'NotImplementedError',
    'True', 'False', 'None', 'self', 'cls',
    # Common string / collection methods — these are NEVER cross-file dependencies
    'append', 'extend', 'insert', 'remove', 'pop', 'clear', 'copy', 'update',
    'get', 'keys', 'values', 'items', 'setdefault', 'add', 'discard',
    'join', 'split', 'strip', 'lstrip', 'rstrip', 'replace', 'startswith',
    'endswith', 'upper', 'lower', 'format', 'encode', 'decode', 'find',
    'index', 'count', 'read', 'write', 'close', 'seek', 'tell', 'flush',
    'exists', 'is_dir', 'is_file', 'mkdir', 'unlink', 'stat', 'resolve',
    'strip', 'splitlines', 'partition', 'rpartition', 'rfind', 'rindex',
    # subprocess / os / common stdlib patterns
    'run', 'check_call', 'check_output', 'Popen', 'communicate',
    'path', 'environ', 'getcwd', 'listdir', 'makedirs', 'walk',
    'loads', 'dumps', 'load', 'dump',
    # Generic patterns that are never meaningful grouping signals
    'args', 'kwargs', 'result', 'data', 'value', 'key', 'name', 'msg',
    'error', 'err', 'exc', 'e', 'ex', 'ret', 'res', 'out', 'output',
    'line', 'lines', 'text', 'content', 'buf', 'size', 'n', 'i', 'j',
})

# Known string/bytes method names — calls of the form `obj.method(...)` are
# NEVER cross-file dependencies regardless of what `obj` is.
_METHOD_NOISE = frozenset({
    'startswith', 'endswith', 'split', 'join', 'strip', 'replace',
    'format', 'encode', 'decode', 'find', 'index', 'count', 'upper', 'lower',
    'append', 'extend', 'pop', 'get', 'items', 'keys', 'values', 'update',
    'read', 'write', 'close', 'seek', 'flush', 'tell',
    'exists', 'is_dir', 'is_file', 'mkdir', 'unlink', 'stat', 'resolve',
    'run', 'communicate', 'loads', 'dumps', 'load', 'dump',
})


def _diff_definitions(hunk: Hunk) -> Set[str]:
    """
    Names *defined or modified* in a hunk — used as group anchors.

    Sources (in order of confidence):
    1. Hunk header trailing context: @@ ... @@ class Foo / def bar
       — the enclosing scope git identifies explicitly. Highest signal.
    2. Any diff line (+, -, or context space) containing def/class.
       Catches: functions being added (+), removed (-), or whose body
       is modified (context line shows the def, body lines are +/-).
    3. Module-level CONSTANT = assignments on + lines.

    This intentionally casts a wider net than just "new definitions"
    because the grouping question is "what symbol does this hunk touch",
    not "what did this hunk add from scratch".
    """
    defs: Set[str] = set()

    # Source 1: hunk header trailing annotation  @@ -x,y +a,b @@ def foo(...):
    # git diff -p puts the enclosing function/class name after the @@ markers
    m = re.search(r'@@[^@]*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)', hunk.header)
    if m:
        defs.add(m.group(1))

    # Source 2: def/class on ANY diff line (added, removed, or context)
    for line in hunk.lines:
        if line.startswith('+++') or line.startswith('---'):
            continue
        # strip the diff prefix (+/-/space)
        code = line[1:] if line and line[0] in ('+', '-', ' ') else line
        m = re.match(r'^\s*(?:async\s+)?def\s+(\w+)', code)
        if m:
            defs.add(m.group(1)); continue
        m = re.match(r'^\s*class\s+(\w+)', code)
        if m:
            defs.add(m.group(1)); continue

    # Source 3: module-level CONSTANT = on + lines only
    for line in hunk.lines:
        if not line.startswith('+') or line.startswith('+++'):
            continue
        code = line[1:]
        m = re.match(r'^([A-Z][A-Z0-9_]{2,})\s*=', code)  # SCREAMING_SNAKE_CASE
        if m:
            defs.add(m.group(1))

    return defs - _SKIP_BUILTINS


def _diff_bare_calls(hunk: Hunk) -> Set[str]:
    """
    Names *called bare* on +/- lines: foo(...) but NOT obj.foo(...).
    These are potential consumers of cross-file definitions.
    """
    calls: Set[str] = set()
    for line in hunk.lines:
        if not (line.startswith('+') or line.startswith('-')):
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        code = line[1:]
        # Remove string literals to avoid false positives inside strings
        code = re.sub(r'["\'].*?["\']', '', code)
        # Find bare calls: word( but NOT preceded by . (which would be method call)
        for m in re.finditer(r'(?<!\.)\b([A-Za-z_]\w*)\s*\(', code):
            name = m.group(1)
            if name not in _SKIP_BUILTINS and name not in _METHOD_NOISE:
                calls.add(name)
    return calls


def _diff_symbols(hunk: Hunk) -> Set[str]:
    """
    Legacy entry point used by _run_impact.
    Returns definitions + bare calls combined — kept broad for impact display
    but _diff_definitions / _diff_bare_calls are used for grouping.
    """
    return (_diff_definitions(hunk) | _diff_bare_calls(hunk)) - _SKIP_BUILTINS


def _file_sym_ranges(path: Path) -> Dict[str, List[Tuple[int, int]]]:
    """{ name: [(start_line, end_line), ...] } from ast."""
    if not path.exists() or not str(path).endswith('.py'):
        return {}
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except SyntaxError:
        return {}
    result: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, 'end_lineno', node.lineno)
            result[node.name].append((node.lineno, end))
    return dict(result)


def _build_callgraph(path: Path) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Returns (calls_dict, callers_dict) for a Python file."""
    if not path.exists() or not str(path).endswith('.py'):
        return {}, {}
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except SyntaxError:
        return {}, {}

    calls: Dict[str, Set[str]] = defaultdict(set)
    cur = [None]

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            prev = cur[0]; cur[0] = node.name
            self.generic_visit(node); cur[0] = prev
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if cur[0]:
                if isinstance(node.func, ast.Name):
                    calls[cur[0]].add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls[cur[0]].add(node.func.attr)
            self.generic_visit(node)

    V().visit(tree)

    callers: Dict[str, Set[str]] = defaultdict(set)
    for caller, callees in calls.items():
        for callee in callees:
            callers[callee].add(caller)

    return dict(calls), dict(callers)


def _hunk_overlapping_syms(hunk: Hunk,
                            sym_ranges: Dict[str, List[Tuple[int, int]]]) -> Set[str]:
    r = hunk.source_line_range()
    if not r:
        return set()
    h_start, h_end = r
    return {sym for sym, ranges in sym_ranges.items()
            if any(s <= h_end and e >= h_start for s, e in ranges)}


def _run_impact(repo: Path, fd: FileDiff, h_idx: int,
                hunk: Hunk, all_files: List[FileDiff]):
    print(f"\n{BOLD}{'─'*22} IMPACT ANALYSIS {'─'*22}{R}")

    sym_ranges        = _file_sym_ranges(repo / fd.path)
    calls_d, callers_d = _build_callgraph(repo / fd.path)

    touched = _hunk_overlapping_syms(hunk, sym_ranges) | _diff_symbols(hunk)
    known   = touched & set(sym_ranges.keys())

    if known:
        print(f"  {BOLD}Symbols touched by this hunk:{R}")
        for sym in sorted(known):
            callees = sorted((calls_d.get(sym, set()) & set(sym_ranges.keys()))
                             - {sym})
            callers = sorted((callers_d.get(sym, set()) & set(sym_ranges.keys()))
                             - {sym})
            print(f"    {CYAN}{sym}{R}")
            if callees:
                print(f"      {DIM}→ calls:     {', '.join(callees[:8])}{R}")
            if callers:
                print(f"      {DIM}← called by: {', '.join(callers[:8])}{R}")
    else:
        raw = _diff_symbols(hunk)
        print(f"  {c_dim('No AST-resolved symbols.')}", end="")
        if raw:
            print(f"  Raw names: {', '.join(sorted(raw)[:10])}")
        else:
            print()

    # Cross-hunk collisions
    all_syms = known | _diff_symbols(hunk)
    collisions: List[Tuple[str, int, Set[str]]] = []

    for other_fd in all_files:
        if other_fd.path == fd.path:
            o_sym_ranges = sym_ranges
        else:
            o_sym_ranges = _file_sym_ranges(repo / other_fd.path)

        for o_idx, o_hunk in enumerate(other_fd.hunks):
            if other_fd.path == fd.path and o_idx == h_idx:
                continue
            o_syms = (_hunk_overlapping_syms(o_hunk, o_sym_ranges)
                      | _diff_symbols(o_hunk))
            shared = all_syms & o_syms
            if shared:
                collisions.append((other_fd.path, o_idx, shared))

    if collisions:
        print(f"\n  {BOLD}{YELLOW}⚠  Other hunks sharing symbols:{R}")
        for (col_file, col_idx, shared) in collisions[:10]:
            tag = c_dim("(this file)") if col_file == fd.path else ""
            print(f"    {YELLOW}hunk #{col_idx+1:>3}{R}  {col_file} {tag}")
            print(f"      {DIM}shared: {CYAN}{', '.join(sorted(shared)[:6])}{R}")
        if len(collisions) > 10:
            print(c_dim(f"    ... and {len(collisions)-10} more"))
        print(c_dim("\n  Tip: consider applying those hunks as a group."))
    else:
        print(c_ok("\n  ✓ No cross-hunk symbol collisions in this session."))

    # ── Group membership diagnostic ──────────────────────────────────────────
    # Show all AST tags on this hunk so mislabeling is immediately visible.
    # E.g. a 111-line daemon/nesting hunk tagged [common_utils] due to one
    # incidental import will show its real tags here (exception_contract,
    # callgraph) and warn the user they may want to reassign it.
    try:
        import sys as _sys, importlib as _imp
        _pg_mod = _imp.import_module("hunk_grouper_ast")
        _diff_p = None
        # Try to locate the condensed diff from session dir
        _session_dirs = sorted(
            (repo / ".gitship" / "hunk-merge").glob("*/condensed.diff")
            if (repo / ".gitship" / "hunk-merge").exists() else [],
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if _session_dirs:
            _diff_p = _session_dirs[0]
        if _diff_p and _diff_p.exists():
            _pg_hunks = _pg_mod.parse_diff(_diff_p)
            _pg_tags  = _pg_mod.group_hunks(_pg_hunks)
            _h_match  = re.match(r'@@ -(\d+)', hunk.header)
            _h_start  = int(_h_match.group(1)) if _h_match else -1
            _my_tags: set = set()
            for _pgh in _pg_hunks:
                if _pgh.file != fd.path:
                    continue
                _pgh_m = re.match(r'@@ -(\d+)', _pgh.header)
                _pgh_start = int(_pgh_m.group(1)) if _pgh_m else -2
                if abs(_pgh_start - _h_start) <= 2:
                    _my_tags = _pg_tags.get(_pgh.id, set())
                    break
            if _my_tags:
                _kind_pri = {"exception_contract": 0, "callgraph": 1,
                             "dependency": 2, "ipc": 3,
                             "symbol_removed": 4, "abstraction": 5}
                _kind_col = {
                    "exception_contract": BMAGENTA, "callgraph": BCYAN,
                    "dependency": BYELLOW, "ipc": CYAN,
                    "symbol_removed": BRED, "abstraction": BGREEN,
                }
                print(f"\n  {BOLD}Detected group tags on this hunk:{R}")
                for _t in sorted(_my_tags,
                                 key=lambda t: (_kind_pri.get(t.kind, 9), t.name)):
                    _col = _kind_col.get(_t.kind, DIM)
                    print(f"    {_col}[{_t.kind}:{_t.name}]{R}")
                if len(_my_tags) > 1:
                    print(c_warn(
                        f"\n  ⚠  Multiple tags detected — grouper may have used "
                        f"the wrong primary label for this hunk.\n"
                        f"     Use [m] at the hunk prompt to reassign it to a "
                        f"different group."
                    ))
    except Exception:
        pass  # grouper not importable or diff not found — silently skip

    print(f"{BOLD}{'─'*61}{R}")


# ── Progress / log ────────────────────────────────────────────────────────────

def _print_progress(state: Dict, files: List[FileDiff]):
    total  = sum(len(f.hunks) for f in files)
    counts = {"theirs": 0, "ours": 0, "edited": 0, "skip": 0}
    for d in state["decisions"]:
        a = d.get("action", "")
        if a in counts:
            counts[a] += 1
    done    = sum(counts.values())
    pending = total - done
    n_taken  = counts["theirs"]
    n_kept   = counts["ours"]
    n_edited = counts["edited"]
    n_skip   = counts["skip"]
    print(f"\n{BOLD}Progress:{R}  {done}/{total} decided  "
          f"({c_ok(str(n_taken) + ' taken')}  "
          f"{c_warn(str(n_kept) + ' kept')}  "
          f"{BCYAN}{n_edited} edited{R}  "
          f"{MAGENTA}{n_skip} skipped{R}  "
          f"{DIM}{pending} pending{R})")


def _print_skip_log(state: Dict):
    skips = [d for d in state["decisions"] if d.get("action") == "skip"]
    if not skips:
        print(c_dim("  (no skipped hunks)")); return
    for s in skips:
        note = f"  → {s['annotation']}" if s.get("annotation") else ""
        print(f"  {MAGENTA}●{R} {s['file']}  hunk #{s['hunk_index']+1}  "
              f"{c_dim(s['hunk_header'][:55])}")
        if note:
            print(f"    {CYAN}{note}{R}")


# ── Undo stack ────────────────────────────────────────────────────────────────

class UndoStack:
    def __init__(self):
        # (file_str, snapshot_bytes_or_None, decision_key)
        self._stack: List[Tuple[str, Optional[bytes], str]] = []

    def push(self, fp: str, snap: Optional[bytes], dk: str):
        self._stack.append((fp, snap, dk))

    def pop(self) -> Optional[Tuple[str, Optional[bytes], str]]:
        return self._stack.pop() if self._stack else None

    def __len__(self):
        return len(self._stack)


def _safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print(); return "q"


# ── Core loop ─────────────────────────────────────────────────────────────────

def run_merge(
    repo: Path,
    source: str,
    target: str,
    file_filter: Optional[str] = None,
    resume: bool = False,
):
    for branch in (source, target):
        r = subprocess.run(["git", "rev-parse", "--verify", branch],
                           cwd=repo, capture_output=True)
        if r.returncode != 0:
            print(f"{RED}Branch not found: {branch}{R}"); return

    cur_r = subprocess.run(["git", "branch", "--show-current"],
                            cwd=repo, capture_output=True, text=True)
    current_branch = cur_r.stdout.strip()
    if current_branch != target:
        print(f"{YELLOW}⚠  On '{current_branch}', not '{target}'.")
        print(f"   Hunks will apply to working tree on '{current_branch}'.{R}")
        if _safe_input("   Continue? [y/N]: ").strip().lower() != "y":
            return

    print(f"\n{BOLD}{'='*64}{R}")
    print(f"{BOLD}  HUNK MERGER  {CYAN}{source}{R}{BOLD} → {BGREEN}{target}{R}")
    print(f"{BOLD}{'='*64}{R}")
    print("  Diffing current state of both branches...")

    diff_text = _get_diff(repo, source, target, file_filter)
    if not diff_text.strip():
        print(f"\n{BGREEN}✓ No differences found.{R}"); return

    files = parse_diff(diff_text)
    if not files:
        print(f"{YELLOW}Diff non-empty but no parseable file diffs found.{R}"); return

    total_hunks = sum(len(f.hunks) for f in files)
    print(f"\n  {len(files)} file(s)  ·  {total_hunks} hunk(s) total\n")
    for fd in files:
        adds = sum(h.adds for h in fd.hunks)
        dels = sum(h.dels for h in fd.hunks)
        print(f"    {CYAN}{fd.path}{R}  {c_dim(f'{len(fd.hunks)} hunks')}  "
              f"{GREEN}+{adds}{R} {RED}-{dels}{R}")

    state = _load_state(repo, source, target)
    undo  = UndoStack()

    # ── Validate existing state against live diff ──────────────────────────────
    if state["decisions"]:
        report = validate_state(repo, state, files)
        if not report["ok"]:
            print_validation_report(report)
            print()
            ans = _safe_input(
                f"  {BOLD}Brain mismatch!{R}  "
                f"[r]eset state and start fresh  [k]eep and continue anyway  [q]uit: "
            ).strip().lower()
            if ans in ("r", "reset"):
                state = {"decisions": [], "meta": {}}
                sp = _state_path(repo, source, target)
                if sp.exists():
                    sp.unlink()
                print(c_ok("  ✓ State reset — starting fresh."))
            elif ans in ("q", "quit"):
                return
            else:
                print(c_warn("  ⚠  Keeping stale state — decisions may not match live hunks."))
        else:
            print_validation_report(report)

    state["meta"] = {
        "source": source, "target": target,
        "file_filter": file_filter or "",
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # .gitignore update handled by _session_dir() on first write

    _safe_input("\n  Press Enter to start... ")

    # ── File loop ──────────────────────────────────────────────────────────────
    for f_no, fd in enumerate(files, 1):
        ours_content = ""
        ours_path = repo / fd.path
        if ours_path.exists():
            try:
                ours_content = ours_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        print(f"\n{BOLD}{'━'*64}{R}")
        print(f"{BOLD}  FILE {f_no}/{len(files)}:  {CYAN}{fd.path}{R}")
        print(f"{BOLD}{'━'*64}{R}")

        already = sum(1 for i in range(len(fd.hunks))
                      if _get_decision(state, fd.path, i) is not None)
        if already == len(fd.hunks):
            print(c_dim("  All hunks already decided.")); continue

        # ── Hunk loop ──────────────────────────────────────────────────────────
        h_idx = 0
        while h_idx < len(fd.hunks):
            hunk     = fd.hunks[h_idx]
            hunk_no  = h_idx + 1
            total    = len(fd.hunks)
            existing = _get_decision(state, fd.path, h_idx)

            if existing and resume and existing["action"] != "skip":
                h_idx += 1; continue

            # ── Prompt loop ────────────────────────────────────────────────────
            while True:
                # Previous decision badge
                prev_badge = ""
                if existing:
                    col = {"theirs": BGREEN, "ours": BYELLOW, "edited": BCYAN,
                           "skip": MAGENTA}.get(existing["action"], DIM)
                    prev_badge = (f"  {col}[{existing['action'].upper()}]{R}"
                                  + (f" {c_dim(existing['annotation'])}"
                                     if existing.get("annotation") else ""))

                # Header
                print(f"\n  {BOLD}Hunk {hunk_no}/{total}{R}  "
                      f"{c_dim(fd.path)}  "
                      f"{GREEN}+{hunk.adds}{R} {RED}-{hunk.dels}{R}"
                      + prev_badge)

                # Hunk content (6 context lines by default)
                _print_hunk(hunk, limit=6)
                print(f"  {c_dim(f'[+] = {source} (theirs/incoming)   [-] = {target} (ours/current)   [t]=apply theirs   [o]=keep ours')}")

                # Legend at the bottom, right above prompt
                print(LEGEND)
                raw = _safe_input(f"  {BOLD}>{R} ").strip().lower()

                # ── QUIT ──────────────────────────────────────────────────────
                if raw in ("q", "quit"):
                    _save_state(repo, state)
                    _print_progress(state, files)
                    print(f"\n{YELLOW}Saved. Resume:  python hunk_merger.py --resume{R}\n")
                    return

                # ── TAKE THEIRS ───────────────────────────────────────────────
                elif raw in ("t", "theirs"):
                    snap = _snapshot(repo, fd.path)
                    ok, err = _apply_hunk(repo, fd.path, hunk)
                    if not ok:
                        print(c_bad("  ✗ git apply failed:"))
                        for line in err.splitlines()[:8]: print(f"    {line}")
                        sub = _safe_input("  [s]kip / [e]dit / [r]etry: ").strip().lower()
                        if sub == "s":
                            note = _safe_input("  Annotation: ").strip()
                            _upsert_decision(state, fd.path, h_idx, "skip", note, hunk.header)
                            _save_state(repo, state); h_idx += 1; break
                        elif sub == "e":
                            raw = "e"; # fall through below
                        else:
                            continue  # retry

                    if raw not in ("e", "edit"):
                        syn_ok, syn_err = _syntax_check(repo, fd.path)
                        if not syn_ok:
                            print(c_bad("  ✗ Syntax error — reverting:"))
                            print(f"    {syn_err}")
                            _restore(repo, fd.path, snap)
                            print(c_warn("  File restored."))
                            sub = _safe_input("  [e]dit / [s]kip / [r]etry: ").strip().lower()
                            if sub == "e":
                                raw = "e"
                            elif sub == "s":
                                note = _safe_input("  Annotation: ").strip()
                                _upsert_decision(state, fd.path, h_idx, "skip",
                                                 f"syntax-fail: {note}", hunk.header)
                                _save_state(repo, state); h_idx += 1; break
                            else:
                                continue
                        else:
                            dk = _key(fd.path, h_idx)
                            undo.push(fd.path, snap, dk)
                            print(c_ok("  ✓ Applied theirs") + c_dim("  (syntax OK — [u] to undo)"))
                            _upsert_decision(state, fd.path, h_idx, "theirs", "", hunk.header)
                            _save_state(repo, state)
                            # Refresh ours_content for subsequent hunks
                            if ours_path.exists():
                                try:
                                    ours_content = ours_path.read_text(
                                        encoding="utf-8", errors="replace")
                                except Exception:
                                    pass
                            h_idx += 1; break
                    # raw == "e": fall through to edit block below

                # ── KEEP OURS ─────────────────────────────────────────────────
                if raw in ("o", "ours"):
                    print(f"  {BYELLOW}→ Keeping ours{R}")
                    _upsert_decision(state, fd.path, h_idx, "ours", "", hunk.header)
                    _save_state(repo, state); h_idx += 1; break

                # ── EDIT ──────────────────────────────────────────────────────
                if raw in ("e", "edit"):
                    edited_lines = _open_editor(hunk, ours_content, fd.path)
                    if edited_lines is None:
                        note = _safe_input("  Empty edit — annotation: ").strip()
                        _upsert_decision(state, fd.path, h_idx, "skip", note, hunk.header)
                        _save_state(repo, state); h_idx += 1; break

                    snap = _snapshot(repo, fd.path)
                    ok, err = _apply_hunk(repo, fd.path, Hunk(hunk.header, edited_lines))
                    if not ok:
                        print(c_bad("  ✗ Edited hunk failed:"))
                        for line in err.splitlines()[:8]: print(f"    {line}")
                        note = _safe_input("  Annotation for skip: ").strip()
                        _upsert_decision(state, fd.path, h_idx, "skip",
                                         f"edit-apply-failed: {note}", hunk.header)
                        _save_state(repo, state); h_idx += 1; break

                    syn_ok, syn_err = _syntax_check(repo, fd.path)
                    if not syn_ok:
                        print(c_bad("  ✗ Syntax error in edited hunk — reverting:"))
                        print(f"    {syn_err}")
                        _restore(repo, fd.path, snap)
                        print(c_warn("  File restored. Try editing again or skip."))
                        continue  # re-prompt

                    dk = _key(fd.path, h_idx)
                    undo.push(fd.path, snap, dk)
                    print(c_ok("  ✓ Applied edited") + c_dim("  (syntax OK)"))
                    _upsert_decision(state, fd.path, h_idx, "edited", "", hunk.header)
                    _save_state(repo, state)
                    if ours_path.exists():
                        try:
                            ours_content = ours_path.read_text(encoding="utf-8",
                                                               errors="replace")
                        except Exception:
                            pass
                    h_idx += 1; break

                # ── SKIP ──────────────────────────────────────────────────────
                if raw in ("s", "skip"):
                    note = _safe_input("  Annotation (optional): ").strip()
                    _upsert_decision(state, fd.path, h_idx, "skip", note, hunk.header)
                    _save_state(repo, state)
                    print(f"  {MAGENTA}→ Skipped{R}" + (f"  {c_dim(note)}" if note else ""))
                    h_idx += 1; break

                # ── UNDO ──────────────────────────────────────────────────────
                if raw in ("u", "undo"):
                    if not undo:
                        print(c_warn("  Nothing to undo this session.")); continue
                    prev_fp, prev_snap, prev_key = undo.pop()
                    _restore(repo, prev_fp, prev_snap)
                    state["decisions"] = [d for d in state["decisions"]
                                          if d.get("key") != prev_key]
                    _save_state(repo, state)
                    undone_idx = int(prev_key.split("::")[-1])
                    print(c_ok(f"  ✓ Undid hunk #{undone_idx+1} in {prev_fp}"))
                    if prev_fp == fd.path and undone_idx == h_idx - 1:
                        h_idx -= 1
                        if ours_path.exists():
                            try:
                                ours_content = ours_path.read_text(
                                    encoding="utf-8", errors="replace")
                            except Exception:
                                pass
                    break  # re-enter hunk loop

                # ── IMPACT ────────────────────────────────────────────────────
                if raw in ("i", "impact"):
                    _run_impact(repo, fd, h_idx, hunk, files); continue

                # ── FULL CONTEXT ──────────────────────────────────────────────
                if raw in ("?", "context"):
                    _show_context(repo, fd.path, hunk, ours_content); continue

                # ── BOTH SIDES ────────────────────────────────────────────────
                if raw in ("b", "both"):
                    snap = _snapshot(repo, fd.path)
                    applied = _show_both(repo, ours_content, hunk, fd.path)
                    if applied:
                        dk = _key(fd.path, h_idx)
                        undo.push(fd.path, snap, dk)
                        _upsert_decision(state, fd.path, h_idx, "edited", "[both]", hunk.header)
                        _save_state(repo, state)
                        if ours_path.exists():
                            try:
                                ours_content = ours_path.read_text(
                                    encoding="utf-8", errors="replace")
                            except Exception:
                                pass
                        h_idx += 1; break
                    continue

                # ── SKIP LOG ──────────────────────────────────────────────────
                if raw in ("l", "log"):
                    _print_skip_log(state); continue

                # ── CHECKPOINT COMMIT ─────────────────────────────────────────
                if raw in ("c", "checkpoint"):
                    _save_state(repo, state)
                    staged_r = subprocess.run(
                        ["git", "diff", "--name-only", "--cached"],
                        cwd=repo, capture_output=True, text=True
                    )
                    if not [l for l in staged_r.stdout.splitlines() if l]:
                        print(c_warn("  ⚠  Nothing staged yet — apply some hunks with [t] first."))
                        continue
                    finalize(repo, state, source, target, is_checkpoint=True)
                    continue

                # unknown key — legend already shown, just re-prompt
                continue

        # ── File summary ───────────────────────────────────────────────────────
        def _n(action):
            return sum(1 for i in range(len(fd.hunks))
                       if (_get_decision(state, fd.path, i) or {}).get("action") == action)
        print(f"\n  {c_dim('File done:')}  "
              f"{BGREEN}{_n('theirs')} taken{R}  "
              f"{BYELLOW}{_n('ours')} kept{R}  "
              f"{BCYAN}{_n('edited')} edited{R}  "
              f"{MAGENTA}{_n('skip')} skipped{R}")

    # ── Session end ────────────────────────────────────────────────────────────
    _save_state(repo, state)
    _print_progress(state, files)
    skips = [d for d in state["decisions"] if d.get("action") == "skip"]
    if skips:
        print(f"\n{BOLD}{MAGENTA}Skipped hunks (revisit with --resume):{R}")
        _print_skip_log(state)

    pending = sum(1 for f in files for i in range(len(f.hunks))
                  if _get_decision(state, f.path, i) is None)
    if pending == 0:
        print(f"\n{BGREEN}✓ All hunks decided.{R}  "
              f"{c_dim('Run finalize to commit → option F below or --finalize')}")
        ans = _safe_input(f"\n  {BOLD}Finalize now?{R} (stage + commit message) [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            finalize(repo, state, source, target)
    else:
        print(f"\n{BYELLOW}⚠  {pending} hunks still pending.{R}  "
              f"Resume with --resume to finish before finalizing.")
    print()


def run_group(repo: Path, group: Dict, all_files: List["FileDiff"],
              state: Dict, source: str, target: str):
    """
    Run the interactive hunk-by-hunk loop over just the hunks in `group`,
    then offer a checkpoint commit labelled with the group symbol + reason.
    """
    sym    = group["sym"]
    reason = group["reason"]
    hunks  = group["hunks"]   # [{file, h_idx, hunk, role, evidence, decision}]

    print(f"\n{BOLD}{'━'*64}{R}")
    print(f"{BOLD}  GROUP: {CYAN}{sym}{R}  {DIM}({reason}){R}")
    print(f"{BOLD}{'━'*64}{R}")
    print(c_dim(f"  {len(hunks)} hunks to review — take/keep/edit/skip each, "
                f"then checkpoint-commit as a unit.\n"))

    undo = UndoStack()
    current_file_content: Dict[str, str] = {}

    def _ours(fp: str) -> str:
        if fp not in current_file_content:
            p = repo / fp
            try:
                current_file_content[fp] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                current_file_content[fp] = ""
        return current_file_content[fp]

    def _refresh(fp: str):
        p = repo / fp
        try:
            current_file_content[fp] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Count pending (undecided) hunks upfront so we can show accurate progress
    def _pending(hs):
        return [e for e in hs if not _get_decision(state, e["file"], e["h_idx"])]

    pending = _pending(hunks)
    done_count = len(hunks) - len(pending)
    if done_count > 0:
        print(f"  {BGREEN}{done_count}/{len(hunks)} already decided{R}  "
              f"{DIM}— skipping to remaining {len(pending)}.{R}")
        print(f"  {DIM}Press [r] at any prompt to review already-decided hunks.{R}\n")

    _review_decided = False  # toggled by [r] key

    h_pos = 0
    while h_pos < len(hunks):
        entry   = hunks[h_pos]
        fp      = entry["file"]
        h_idx   = entry["h_idx"]
        hunk    = entry["hunk"]
        role    = entry["role"]
        existing = _get_decision(state, fp, h_idx)

        # Skip already-decided hunks unless the user asked to review them
        if existing and not _review_decided:
            h_pos += 1
            continue

        role_col = {_ROLE_DEF: BGREEN, _ROLE_CALL: CYAN, _ROLE_SCOPE: DIM}
        rc = role_col.get(role, DIM)

        # Recount pending each iteration so the counter stays accurate
        _done_now = sum(1 for e in hunks if _get_decision(state, e["file"], e["h_idx"]))
        _pend_now = len(hunks) - _done_now
        _pct = int(100 * _done_now / len(hunks)) if hunks else 100

        while True:
            prev_badge = ""
            if existing:
                col = {"theirs": BGREEN, "ours": BYELLOW, "edited": BCYAN,
                       "skip": MAGENTA}.get(existing["action"], DIM)
                prev_badge = f"  {col}[{existing['action'].upper()}]{R}"

            progress = (f"{BGREEN}{_done_now}{R}{DIM}/{len(hunks)}{R} "
                        f"{DIM}({_pct}% done,  {_pend_now} remaining){R}")
            print(f"\n  {BOLD}Hunk {h_pos+1}/{len(hunks)}{R}  {progress}  "
                  f"{rc}{role}{R}  {c_dim(fp)}  "
                  f"{GREEN}+{hunk.adds}{R} {RED}-{hunk.dels}{R}"
                  + prev_badge)
            if entry.get("evidence"):
                ev = entry["evidence"]
                ev_col = GREEN if ev.startswith("+") else RED
                print(f"  {ev_col}{DIM}{ev[:72]}{R}")
            _print_hunk(hunk, limit=6)
            print(LEGEND)
            raw = _safe_input(f"  {BOLD}>{R} ").strip().lower()

            if raw in ("q", "quit"):
                _save_state(repo, state)
                print(f"\n{YELLOW}Saved. Group review paused.{R}\n")
                return False  # did not finish group

            elif raw in ("r", "review"):
                _review_decided = not _review_decided
                h_pos = 0  # restart from beginning to show all
                status = f"{BGREEN}ON{R}" if _review_decided else f"{YELLOW}OFF{R}"
                print(f"  Review-decided mode: {status}  (restarting from hunk 1)")
                break  # break inner while, outer while resets h_pos

            elif raw in ("t", "theirs"):
                snap = _snapshot(repo, fp)
                ok, err = _apply_hunk(repo, fp, hunk)
                if not ok:
                    print(c_bad("  ✗ Apply failed:"))
                    for line in err.splitlines()[:6]: print(f"    {line}")
                    sub = _safe_input("  [s]kip / [e]dit / [r]etry: ").strip().lower()
                    if sub == "s":
                        note = _safe_input("  Annotation: ").strip()
                        _upsert_decision(state, fp, h_idx, "skip", note, hunk.header)
                        _save_state(repo, state); h_pos += 1; break
                    elif sub == "e":
                        raw = "e"
                    else:
                        continue
                if raw not in ("e", "edit"):
                    syn_ok, syn_err = _syntax_check(repo, fp)
                    if not syn_ok:
                        print(c_bad("  ✗ Syntax error — reverting:"))
                        print(f"    {syn_err}")
                        _restore(repo, fp, snap)
                        sub = _safe_input("  [e]dit / [s]kip / [r]etry: ").strip().lower()
                        if sub == "e": raw = "e"
                        elif sub == "s":
                            note = _safe_input("  Annotation: ").strip()
                            _upsert_decision(state, fp, h_idx, "skip",
                                             f"syntax-fail: {note}", hunk.header)
                            _save_state(repo, state); h_pos += 1; break
                        else: continue
                    else:
                        dk = _key(fp, h_idx)
                        undo.push(fp, snap, dk)
                        # tag this decision with the group it belongs to
                        _upsert_decision(state, fp, h_idx, "theirs", f"[group:{sym}]", hunk.header)
                        _save_state(repo, state)
                        _refresh(fp)
                        print(c_ok("  ✓ Applied theirs") + c_dim("  (syntax OK)"))
                        h_pos += 1; break

            if raw in ("o", "ours"):
                _upsert_decision(state, fp, h_idx, "ours", f"[group:{sym}]", hunk.header)
                _save_state(repo, state)
                print(f"  {BYELLOW}→ Keeping ours{R}")
                h_pos += 1; break

            if raw in ("e", "edit"):
                edited_lines = _open_editor(hunk, _ours(fp), fp)
                if edited_lines is None:
                    note = _safe_input("  Empty edit — annotation: ").strip()
                    _upsert_decision(state, fp, h_idx, "skip", note, hunk.header)
                    _save_state(repo, state); h_pos += 1; break
                snap = _snapshot(repo, fp)
                ok, err = _apply_hunk(repo, fp, Hunk(hunk.header, edited_lines))
                if not ok:
                    print(c_bad("  ✗ Edited hunk failed:"))
                    for line in err.splitlines()[:6]: print(f"    {line}")
                    _upsert_decision(state, fp, h_idx, "skip",
                                     f"edit-apply-failed", hunk.header)
                    _save_state(repo, state); h_pos += 1; break
                syn_ok, syn_err = _syntax_check(repo, fp)
                if not syn_ok:
                    print(c_bad("  ✗ Syntax error — reverting:"))
                    _restore(repo, fp, snap); continue
                dk = _key(fp, h_idx)
                undo.push(fp, snap, dk)
                _upsert_decision(state, fp, h_idx, "edited", f"[group:{sym}]", hunk.header)
                _save_state(repo, state)
                _refresh(fp)
                print(c_ok("  ✓ Applied edited"))
                h_pos += 1; break

            if raw in ("s", "skip"):
                note = _safe_input("  Annotation (optional): ").strip()
                _upsert_decision(state, fp, h_idx, "skip", note, hunk.header)
                _save_state(repo, state)
                print(f"  {MAGENTA}→ Skipped{R}")
                h_pos += 1; break

            if raw in ("u", "undo"):
                if not undo:
                    print(c_warn("  Nothing to undo this session.")); continue
                prev_fp, prev_snap, prev_key = undo.pop()
                _restore(repo, prev_fp, prev_snap)
                state["decisions"] = [d for d in state["decisions"]
                                      if d.get("key") != prev_key]
                _save_state(repo, state)
                undone_idx = int(prev_key.split("::")[-1])
                print(c_ok(f"  ✓ Undid hunk #{undone_idx+1} in {prev_fp}"))
                if prev_fp == fp and undone_idx == h_idx:
                    if h_pos > 0: h_pos -= 1
                    _refresh(fp)
                break

            if raw in ("i", "impact"):
                # find the FileDiff for this hunk
                fd_obj = next((f for f in all_files if f.path == fp), None)
                if fd_obj:
                    _run_impact(repo, fd_obj, h_idx, hunk, all_files)
                continue

            if raw in ("m", "move"):
                # ── REASSIGN THIS HUNK TO A DIFFERENT GROUP ────────────────
                # Lets the user move a mislabeled hunk (e.g. one tagged
                # [common_utils] because of one incidental import) into the
                # group that actually matches its semantic purpose.
                _all_groups = state.get("_runtime_groups", [])
                if not _all_groups:
                    print(c_warn("  No group list available (only works in group mode)."))
                    continue
                print(f"\n  {BOLD}Move hunk {h_pos+1} to a different group:{R}")
                print(f"  {DIM}Current group: {CYAN}{sym}{R}")
                print(f"  {DIM}0. Eject — remove from this group (goes to ungrouped){R}")
                for _gi, _g in enumerate(_all_groups, 1):
                    _cross = "★" if _g["cross_file"] else "○"
                    _gsym  = _g["sym"]
                    _gn    = len(_g["hunks"])
                    _here  = f"  {CYAN}← current{R}" if _gsym == sym else ""
                    print(f"  {_gi}. {_cross} {CYAN}{_gsym}{R}  "
                          f"{c_dim(_g['reason'][:55])}  ({_gn}h){_here}")
                _choice = _safe_input(f"\n  {BOLD}Group # (Enter to cancel):{R} ").strip()
                if not _choice:
                    continue
                if _choice == "0":
                    _upsert_decision(state, fp, h_idx, "skip",
                                     f"[ejected from group:{sym}]", hunk.header)
                    _save_state(repo, state)
                    print(c_warn(f"  → Ejected from [{sym}] — will appear in ungrouped."))
                    h_pos += 1; break
                try:
                    _tidx = int(_choice) - 1
                    _tg   = _all_groups[_tidx]
                except (ValueError, IndexError):
                    print(c_warn("  Invalid choice.")); continue
                _tgsym = _tg["sym"]
                if _tgsym == sym:
                    print(c_dim("  Already in this group.")); continue
                # Append to target group hunk list so it shows up when that
                # group is reviewed. Mark with reassigned_from for audit trail.
                _new_entry = dict(entry)
                _new_entry["_reassigned_from"] = sym
                # Avoid duplicate if already there
                _already = any(
                    e["file"] == fp and e["h_idx"] == h_idx
                    for e in _tg["hunks"]
                )
                if not _already:
                    _tg["hunks"].append(_new_entry)
                # Eject from current group via skip annotation
                _upsert_decision(state, fp, h_idx, "skip",
                                 f"[moved to group:{_tgsym}]", hunk.header)
                _save_state(repo, state)
                print(c_ok(f"  ✓ Moved to [{_tgsym}] — review it there."))
                h_pos += 1; break

            if raw in ("?", "context"):
                _show_context(repo, fp, hunk, _ours(fp)); continue

            if raw in ("b", "both"):
                snap = _snapshot(repo, fp)
                applied = _show_both(repo, _ours(fp), hunk, fp)
                if applied:
                    dk = _key(fp, h_idx)
                    undo.push(fp, snap, dk)
                    _upsert_decision(state, fp, h_idx, "edited", f"[both][group:{sym}]", hunk.header)
                    _save_state(repo, state)
                    _refresh(fp)
                    h_pos += 1; break
                continue

            if raw in ("l", "log"):
                _print_skip_log(state); continue

            # ── ADD THIS BLOCK ──────────────────────────────────────────────
            if raw in ("c", "checkpoint"):
                _save_state(repo, state)
                staged_r = subprocess.run(
                    ["git", "diff", "--name-only", "--cached"],
                    cwd=repo, capture_output=True, text=True
                )
                if not[l for l in staged_r.stdout.splitlines() if l]:
                    print(c_warn("  ⚠  Nothing staged yet — apply some hunks with [t] first."))
                    continue
                finalize(repo, state, source, target, is_checkpoint=True)
                continue
            # ────────────────────────────────────────────────────────────────

            continue  # unknown key

    # ── All hunks in this group reviewed — offer checkpoint commit ───────────
    print(f"\n{BOLD}{BMAGENTA}  GROUP COMPLETE: {sym}{R}")
    taken_in_group  = [e for e in hunks
                       if (_get_decision(state, e["file"], e["h_idx"]) or {})
                          .get("action") == "theirs"]
    kept_in_group   = [e for e in hunks
                       if (_get_decision(state, e["file"], e["h_idx"]) or {})
                          .get("action") == "ours"]
    edited_in_group = [e for e in hunks
                       if (_get_decision(state, e["file"], e["h_idx"]) or {})
                          .get("action") == "edited"]
    skipped_in_group= [e for e in hunks
                       if (_get_decision(state, e["file"], e["h_idx"]) or {})
                          .get("action") == "skip"]

    print(f"  {BGREEN}{len(taken_in_group)} taken{R}  "
          f"{BYELLOW}{len(kept_in_group)} kept{R}  "
          f"{BCYAN}{len(edited_in_group)} edited{R}  "
          f"{MAGENTA}{len(skipped_in_group)} skipped{R}")

    staged_r = subprocess.run(["git", "diff", "--name-only", "--cached"],
                              cwd=repo, capture_output=True, text=True)
    staged_files = [l for l in staged_r.stdout.splitlines() if l]

    if not staged_files:
        print(c_dim("  Nothing staged — all hunks were kept-ours or skipped."))
        _safe_input(f"\n  {DIM}Press Enter to return to groups...{R} ")
        return True

    print(f"\n  {BOLD}Staged ({len(staged_files)} files):{R}")
    for sf in staged_files:
        print(f"    {BGREEN}{sf}{R}")

    # Pre-built commit message from group data
    file_lines = "\n".join(f"  {e['file']} [{e['role']}]" for e in hunks
                             if (_get_decision(state, e["file"], e["h_idx"]) or {})
                                .get("action") == "theirs")
    group_msg = (
        f"merge[group]: port {sym} — {source} → {target}\n\n"
        f"Dependency group: {reason}\n\n"
        f"Hunks: {len(taken_in_group)} taken  "
        f"{len(kept_in_group)} kept-ours  "
        f"{len(edited_in_group)} edited  "
        f"{len(skipped_in_group)} skipped\n"
        + (f"\nFiles changed:\n{file_lines}\n" if file_lines else "")
        + ("\nSkipped hunks:\n"
           + "\n".join(f"  {e['file']} hunk #{e['h_idx']+1}"
                        for e in skipped_in_group)
           if skipped_in_group else "")
        + f"\n\n[gitship-hunk-merger-group:{sym}]"
    )

    print(f"\n  {BOLD}Checkpoint commit for this group?{R}")
    print(f"  c. Commit  e. Edit message  s. Skip (keep staged for later)  0. Cancel")
    ans = _safe_input(f"\n  {BOLD}>{R} ").strip().lower()

    if ans in ("e", "edit"):
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                         delete=False, prefix="hm_group_") as f:
            f.write(group_msg); tmp = f.name
        subprocess.run([editor, tmp])
        group_msg = Path(tmp).read_text()
        Path(tmp).unlink(missing_ok=True)
        ans = "c"

    if ans in ("c", "commit"):
        r = subprocess.run(["git", "commit", "-m", group_msg],
                           cwd=repo, capture_output=True, text=True)
        if r.returncode == 0:
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                  cwd=repo, capture_output=True, text=True).stdout.strip()
            print(c_ok(f"\n  ✓ Group committed!  {c_dim(sha)}"))
            print(c_dim(f"  {group_msg.splitlines()[0][:72]}"))
            
            # ── PUSH PROMPT ───────────────────────────────────────────────
            ans_push = _safe_input(f"\n  {BOLD}Push to remote?{R} [y/N]: ").strip().lower()
            if ans_push in ("y", "yes"):
                print(f"  {DIM}Pushing to origin {target}...{R}")
                subprocess.run(["git", "push", "origin", target], cwd=repo)
            # ──────────────────────────────────────────────────────────────
        else:
            print(c_bad(f"\n  ✗ Commit failed: {r.stderr.strip()}"))
    else:
        print(c_dim("  Staged changes kept — commit manually or via [F]inalize."))

    _safe_input(f"\n  {DIM}Press Enter to return to groups...{R} ")
    return True



# ── Grouped hunks view ────────────────────────────────────────────────────────

# Role tags for why a hunk is in a group
_ROLE_DEF   = "defines"   # hunk's owning function defines/implements the symbol
_ROLE_CALL  = "calls"     # hunk's owning function calls the symbol cross-file
_ROLE_SCOPE = "scope"     # hunk is in the same scope/class as the symbol (same-file context)


def _build_full_repo_call_graph(repo: Path) -> Tuple[
    Dict[str, Dict[str, Tuple[int, int]]],   # func_spans[file][fname] = (start, end)
    Dict[str, Set[str]],                      # call_graph[file::fname] = {callee, ...}
    Dict[str, Set[str]],                      # defined_in[fname] = {file::fname, ...}
]:
    """
    Scan every .py file under repo/src with AST and build:
      func_spans  — line ranges for every function in every file
      call_graph  — outbound call edges per fully-qualified function name
      defined_in  — reverse index: bare name → all fq names that define it
    """
    func_spans: Dict[str, Dict[str, Tuple[int, int]]] = defaultdict(dict)
    call_graph: Dict[str, Set[str]]  = defaultdict(set)
    defined_in: Dict[str, Set[str]]  = defaultdict(set)

    src_root = repo / "src"
    scan_root = src_root if src_root.exists() else repo

    for f in sorted(scan_root.rglob("*.py")):
        try:
            src  = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
            fkey = str(f.relative_to(repo))
            lines = src.splitlines()

            funcs = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            funcs.sort(key=lambda n: n.lineno)

            for i, node in enumerate(funcs):
                end = funcs[i + 1].lineno - 1 if i + 1 < len(funcs) else len(lines)
                fname  = node.name
                fqname = fkey + "::" + fname
                func_spans[fkey][fname] = (node.lineno, end)
                defined_in[fname].add(fqname)

                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Attribute):
                            call_graph[fqname].add(child.func.attr)
                        elif isinstance(child.func, ast.Name):
                            call_graph[fqname].add(child.func.id)
        except Exception:
            pass

    return dict(func_spans), dict(call_graph), dict(defined_in)


def _owning_func(func_spans: Dict[str, Tuple[int, int]], line: int) -> Optional[str]:
    """Return the name of the innermost function that contains `line`."""
    best: Optional[str] = None
    best_start = -1
    for fname, (s, e) in func_spans.items():
        if s <= line <= e and s > best_start:
            best, best_start = fname, s
    return best


def _build_group_map(repo: Path, files: List["FileDiff"], state: Dict,
                     debug: bool = False) -> List[Dict]:
    """
    AST multi-tagging adapter — delegates to hunk_grouper_ast.group_hunks().

    Returns the same List[Dict] format as the old union-find implementation
    so the rest of hunk_merger.py (show_groups, run_group, display) is unchanged.

    Design rules (see migration plan):
      • No union-find.  No transitivity hairballs.
      • No cross-file grouping on bare string tokens.
      • Same-file call edges never produce cross-file tags.
      • A hunk may belong to multiple groups (multi-tagging).
    """
    import sys as _sys
    import importlib as _importlib
    from pathlib import Path as _Path

    # ── Import grouper (sibling file, same directory as hunk_merger.py) ──────
    _here = _Path(__file__).parent
    _spec = _importlib.util.spec_from_file_location(
        "hunk_grouper_ast", _here / "hunk_grouper_ast.py"
    )
    _mod = _importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    # ── Convert FileDiff/Hunk objects → grouper's Hunk objects ──────────────
    # The grouper's parse_diff() produces its own Hunk dataclass.
    # We reconstruct the same data from the already-parsed FileDiff objects
    # so we don't have to re-run git diff.
    grouper_hunks = []
    hunk_id = 0
    for fd in files:
        for h_idx, hunk in enumerate(fd.hunks):
            gh = _mod.Hunk(
                id=hunk_id,
                file=fd.path,
                header=hunk.header,
            )
            for ln in hunk.lines:
                if ln.startswith("---") or ln.startswith("+++"):
                    continue
                if ln.startswith("-"):
                    gh.removed.append(ln[1:])
                elif ln.startswith("+"):
                    gh.added.append(ln[1:])
                else:
                    gh.context.append(ln.lstrip(" "))
            grouper_hunks.append((gh, fd.path, h_idx, hunk))
            hunk_id += 1

    just_hunks = [t[0] for t in grouper_hunks]

    if debug:
        print(c_dim(f"    [groups] AST grouper: {len(just_hunks)} hunks from "
                    f"{len(files)} files"), flush=True)
        print(c_dim("    [groups] running 6 tag extractors (no union-find)…"), flush=True)

    # ── Run grouper ───────────────────────────────────────────────────────────
    # Ensure we pass the actual git worktree root, not a subdirectory like .gitship.
    # repo might be the .gitship dir itself if called from a subdirectory context.
    _repo_root = repo
    for _candidate in [repo, repo.parent, repo.parent.parent]:
        if (_candidate / ".git").exists():
            _repo_root = _candidate
            break
    tag_map = _mod.group_hunks(just_hunks, repo_path=_repo_root)   # Dict[hunk_id -> Set[GroupTag]]

    # ── Invert tag map → groups list ─────────────────────────────────────────
    # tag -> list of (grouper_hunk, file_path, h_idx, ui_hunk)
    from collections import defaultdict as _dd
    tag_to_entries: dict = _dd(list)
    for gh, fp, h_idx, ui_hunk in grouper_hunks:
        for tag in tag_map.get(gh.id, set()):
            tag_to_entries[tag].append((gh, fp, h_idx, ui_hunk))

    groups: List[Dict] = []

    kind_verb = {
        "dependency":         "adopts module",
        "ipc":                "IPC message type",
        "symbol_removed":     "symbol removed",
        "exception_contract": "exception contract",
        "abstraction":        "refactored to",
        "callgraph":          "cross-function calls to",
    }

    for tag, entries in sorted(tag_to_entries.items(), key=lambda x: -len(x[1])):
        if len(entries) < 2:
            continue

        files_in = {fp for _, fp, _, _ in entries}
        is_cross = len(files_in) > 1

        # Callgraph tags are same-file by design — never emit as cross-file
        if tag.kind == "callgraph" and is_cross:
            continue

        verb = kind_verb.get(tag.kind, tag.kind)
        if is_cross:
            reason = (
                f"{verb} {tag.name!r} across "
                + ", ".join(f.split("/")[-1] for f in sorted(files_in))
            )
            sym = f"[{tag.name}]"
        else:
            fname = list(files_in)[0].split("/")[-1]
            reason = (
                f"{verb} {tag.name!r} "
                f"across {len(entries)} hunks in {fname}"
            )
            sym = tag.name if tag.kind == "callgraph" else f"[{tag.name}]"

        hunk_entries: List[Dict] = []
        seen: set = set()
        for gh, fp, h_idx, ui_hunk in sorted(entries, key=lambda t: (t[1], t[2])):
            key = (fp, h_idx)
            if key in seen:
                continue
            seen.add(key)

            # Role: callgraph definers get _ROLE_DEF, others are _ROLE_CALL peers
            if tag.kind == "callgraph":
                # The hunk that IS in the callee function body gets DEF role.
                # Best-effort: check if the header context names the callee.
                is_def = bool(re.search(
                    r'@@[^@]*@@\s*(?:async\s+)?def\s+' + re.escape(tag.name),
                    gh.header
                ))
                role = _ROLE_DEF if is_def else _ROLE_CALL
            elif tag.kind == "ipc":
                role = _ROLE_DEF if any(tag.name in l for l in gh.added) else _ROLE_CALL
            else:
                role = _ROLE_CALL

            ev = ""
            for ln in ui_hunk.lines:
                if (ln.startswith("+") and not ln.startswith("+++")) or \
                   (ln.startswith("-") and not ln.startswith("---")):
                    ev = ln.rstrip()[:80]
                    break

            hunk_entries.append({
                "file":     fp,
                "h_idx":    h_idx,
                "hunk":     ui_hunk,
                "role":     role,
                "evidence": ev,
                "decision": _get_decision(state, fp, h_idx),
            })

        hunk_entries.sort(key=lambda e: (e["role"] != _ROLE_DEF, e["file"], e["h_idx"]))

        groups.append({
            "sym":        sym,
            "reason":     reason,
            "hunks":      hunk_entries,
            "cross_file": is_cross,
            "n_files":    len(files_in),
        })

    # Sort: cross-file first, then by hunk count descending
    groups.sort(key=lambda g: (-int(g["cross_file"]), -len(g["hunks"])))

    if debug:
        cross_c = sum(1 for g in groups if g["cross_file"])
        same_c  = sum(1 for g in groups if not g["cross_file"])
        print(c_dim(f"    [dbg] final groups: {len(groups)} total  "
                    f"({cross_c} cross-file, {same_c} same-file)"), flush=True)
        # Ungrouped summary
        grouped_keys = {(e["file"], e["h_idx"]) for g in groups for e in g["hunks"]}
        ungrouped = [
            (fd.path, h_idx)
            for fd in files
            for h_idx, _ in enumerate(fd.hunks)
            if (fd.path, h_idx) not in grouped_keys
        ]
        if ungrouped:
            by_file: Dict[str, int] = _dd(int)
            for fp, _ in ungrouped:
                by_file[fp.split("/")[-1]] += 1
            print(c_dim(f"    [dbg] ungrouped hunks: {len(ungrouped)} — "
                        + ", ".join(f"{fn}:{cnt}" for fn, cnt in
                                    sorted(by_file.items(), key=lambda kv: -kv[1])[:8])), flush=True)

    return groups



def _print_group_summary_table(groups: List[Dict], ungrouped_entries: List[Dict], state: Dict):
    """Prints the compact summary table of all groups and their progress."""
    # Compute overall progress across ALL hunks (grouped + ungrouped)
    all_hunk_keys = [
        (e["file"], e["h_idx"])
        for g in groups for e in g["hunks"]
    ] + [(e["file"], e["h_idx"]) for e in ungrouped_entries]
    total_hunks  = len(all_hunk_keys)
    total_done   = sum(1 for f, i in all_hunk_keys if _get_decision(state, f, i))
    total_pct    = int(100 * total_done / total_hunks) if total_hunks else 100

    pct_col = BGREEN if total_pct == 100 else (BYELLOW if total_pct >= 50 else YELLOW)
    print(f"\n{BOLD}{'='*72}{R}")
    print(f"  {BOLD}Overall progress:{R}  "
          f"{pct_col}{total_done}/{total_hunks} hunks decided  ({total_pct}%){R}"
          + (f"  {BGREEN}✓ COMPLETE{R}" if total_pct == 100 else
             f"  {DIM}{total_hunks - total_done} remaining{R}"))
    print(f"{BOLD}{'='*72}{R}")
    print(c_dim(f"  {'#':>3}  {'★/○':<3}  {'symbol':<28}  {'done':>4}/{'tot':<4}  {'%':>3}  files"))
    print(c_dim(f"  {'─'*3}  {'─'*3}  {'─'*28}  {'─'*9}  {'─'*3}  {'─'*28}"))
    for g_no, g in enumerate(groups, 1):
        star = "★" if g["cross_file"] else "○"
        scol = YELLOW if g["cross_file"] else DIM
        files_str = ", ".join(sorted({e["file"].split("/")[-1] for e in g["hunks"]}))
        sym_str = g["sym"][:28]
        g_done = sum(1 for e in g["hunks"] if _get_decision(state, e["file"], e["h_idx"]))
        g_tot  = len(g["hunks"])
        g_pct  = int(100 * g_done / g_tot) if g_tot else 100
        g_col  = BGREEN if g_pct == 100 else DIM
        done_str = f"{g_col}{g_done}{R}{DIM}/{g_tot}{R}"
        pct_str  = f"{g_col}{g_pct:>3}%{R}"
        print(f"  {DIM}{g_no:>3}{R}  {scol}{star}{R}    {CYAN}{sym_str:<28}{R}  "
              f"{done_str:<5}      {pct_str}  {DIM}{files_str[:28]}{R}")
    if ungrouped_entries:
        ug_files = ", ".join(sorted({e["file"].split("/")[-1] for e in ungrouped_entries})[:3])
        ug_done = sum(1 for e in ungrouped_entries if _get_decision(state, e["file"], e["h_idx"]))
        ug_tot  = len(ungrouped_entries)
        ug_pct  = int(100 * ug_done / ug_tot) if ug_tot else 100
        ug_col  = BGREEN if ug_pct == 100 else DIM
        ug_done_str = f"{ug_col}{ug_done}{R}{DIM}/{ug_tot}{R}"
        ug_pct_str  = f"{ug_col}{ug_pct:>3}%{R}"
        print(f"  {DIM}  u{R}  {DIM}—{R}    {DIM}{'(ungrouped)':<28}{R}  "
              f"{ug_done_str:<5}      {ug_pct_str}  {DIM}{ug_files[:28]}{R}")
    print(f"{BOLD}{'─'*72}{R}")


def show_groups(repo: Path,
                source: Optional[str] = None,
                target: Optional[str] = None,
                file_filter: Optional[str] = None,
                interactive: bool = True):
    """
    Cross-file symbol grouping: clusters hunks whose symbols are actually
    defined in one hunk and called (bare, not as obj.method) in another.
    Works with or without a saved session.
    If interactive=True (default), shows a picker to review groups in-place.
    """
    _boot = _load_state(repo)
    _bm   = _boot.get("meta", {})
    source      = source      or _bm.get("source",      "developer-port")
    target      = target      or _bm.get("target",      "development")
    file_filter = file_filter or _bm.get("file_filter") or None
    state = _load_state(repo, source, target)
    meta  = state.get("meta", {})
    # Always stamp source/target into meta so _save_state (called inside run_group)
    # can find the correct path even when no prior state file exists.
    state["meta"] = dict(meta, source=source, target=target)
    _save_state(repo, state)  # write initial meta immediately

    print(f"\n{DIM}  Diffing {source} → {target}"
          + (f"  (filter: {file_filter})" if file_filter else "")
          + f"  — analysing cross-file dependencies...{R}")

    diff_text = _get_diff(repo, source, target, file_filter)
    files     = parse_diff(diff_text)
    if not files:
        print("No diff to group."); return

    groups = _build_group_map(repo, files, state, debug=DEBUG_GROUPS)

    if not groups:
        print(c_ok("\n✓ No cross-file symbol dependencies found."))
        print(c_dim("  All hunks appear self-contained — safe to review file-by-file."))
        print(f"{BOLD}{'─'*64}{R}"); return

    cross = [g for g in groups if g["cross_file"]]
    same  = [g for g in groups if not g["cross_file"]]

    print(f"\n{BOLD}{'═'*64}{R}")
    print(f"{BOLD}  DEPENDENCY GROUPS  "
          f"({len(cross)} cross-file  ·  {len(same)} same-file){R}")
    print(f"{BOLD}{'═'*64}{R}")
    print(c_dim("  ★ = cross-file  take these as a group or risk broken imports/calls"))
    print(c_dim("  ○ = same-file   shared data contract — review together"))

    role_col   = {_ROLE_DEF: BGREEN, _ROLE_CALL: CYAN}
    role_label = {_ROLE_DEF: "defines", _ROLE_CALL: "calls  "}

    # ── Detailed list ─────────────────────────────────────────────────────────
    print()
    for g_no, g in enumerate(groups, 1):
        star = f"{YELLOW}★{R}" if g["cross_file"] else f"{DIM}○{R}"
        n_hunks = len(g['hunks']); n_files = g['n_files']
        print(f"  {BOLD}{DIM}{g_no:>2}.{R} {star} {BOLD}{CYAN}{g['sym']}{R}  {DIM}({n_hunks} hunks, {n_files} file(s)){R}")
        print(f"      {DIM}why: {g['reason']}{R}")

        for entry in g["hunks"]:
            fp   = entry["file"]; h_idx = entry["h_idx"]
            hunk = entry["hunk"]; role  = entry["role"]
            dec  = entry["decision"]

            if dec:
                dcol  = {"theirs": BGREEN, "ours": BYELLOW,
                         "edited": BCYAN, "skip": MAGENTA}.get(dec["action"], DIM)
                badge = f" {dcol}[{dec['action'].upper()}]{R}"
            else:
                badge = f" {DIM}[pending]{R}"

            rc  = role_col.get(role, DIM)
            rl  = role_label.get(role, role)
            short_hdr = hunk.header[:45].strip()
            print(f"    {rc}{rl}{R}  {YELLOW}#{h_idx+1:>3}{R}  {fp}{badge}")
            print(f"            {c_dim(short_hdr)}  {GREEN}+{hunk.adds}{R} {RED}-{hunk.dels}{R}")

        print()

    # ── Build ungrouped list for display and picking ─────────────────────────
    grouped_keys = {(e["file"], e["h_idx"]) for g in groups for e in g["hunks"]}
    ungrouped_entries = [
        {"file": fd.path, "h_idx": h_idx, "hunk": fd.hunks[h_idx]}
        for fd in files
        for h_idx in range(len(fd.hunks))
        if (fd.path, h_idx) not in grouped_keys
    ]

    # ── Compact summary index at bottom ─────────────────────────────────────
    _print_group_summary_table(groups, ungrouped_entries, state)

    if not interactive:
        print(c_dim("  Tip: use [c] checkpoint after each ★ group to keep commits coherent."))
        return

    # ── Interactive group picker ───────────────────────────────────────────────
    prompt_suffix = " / u=ungrouped" if ungrouped_entries else ""
    print(c_dim(f"  Enter a group number to review its hunks interactively{prompt_suffix},"))
    print(c_dim("  or press Enter / 0 to exit."))
    while True:
        raw = _safe_input(f"\n  {BOLD}Group # (or 0/Enter to exit):{R} ").strip().lower()
        if not raw or raw == "0":
            break

        # ── Ungrouped picker ──────────────────────────────────────────────────
        if raw == "u" and ungrouped_entries:
            # Synthesise a temporary group dict so run_group can handle it
            ug_files_set = sorted({e["file"] for e in ungrouped_entries})
            ug_group = {
                "sym":        "(ungrouped)",
                "reason":     f"{len(ungrouped_entries)} hunks with no detected semantic relationship",
                "cross_file": len(ug_files_set) > 1,
                "n_files":    len(ug_files_set),
                "hunks":      [
                    {"file": e["file"], "h_idx": e["h_idx"],
                     "hunk": e["hunk"], "role": _ROLE_CALL,
                     "decision": _get_decision(state, e["file"], e["h_idx"])}
                    for e in ungrouped_entries
                ],
            }
            run_group(repo, ug_group, files, state, source, target)
            state = _load_state(repo)
            _print_group_summary_table(groups, ungrouped_entries, state)
            print(c_dim("  Enter a group number, u for ungrouped, or 0/Enter to exit."))
            continue

        try:
            pick = int(raw) - 1
        except ValueError:
            print(c_warn("  Enter a number, or u for ungrouped.")); continue
        if pick < 0 or pick >= len(groups):
            print(c_warn(f"  Out of range (1–{len(groups)}).")); continue

        g = groups[pick]
        # Inject full group list into state so [m]ove handler can see all groups.
        # This is runtime-only — not persisted to disk (groups are rebuilt each run).
        state["_runtime_groups"] = groups
        run_group(repo, g, files, state, source, target)

        # Refresh decisions after run_group (state was mutated in place via _upsert)
        state = _load_state(repo)

        # Re-display the main summary table
        _print_group_summary_table(groups, ungrouped_entries, state)
        print(c_dim("  Enter another group number, or 0/Enter to exit."))


# ── Commit message generation ─────────────────────────────────────────────────

def _generate_hunk_commit_message(repo: Path, state: Dict,
                                   source: str, target: str) -> str:
    """
    Build a rich commit message from hunk merger state + git diff stats.
    Mirrors merge_message.py logic but driven by our per-hunk decisions.
    """
    decisions = state.get("decisions", [])
    meta      = state.get("meta", {})

    # Per-file decision summary
    file_map: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for d in decisions:
        file_map[d["file"]][d["action"]] += 1

    taken   = [d for d in decisions if d["action"] == "theirs"]
    kept    = [d for d in decisions if d["action"] == "ours"]
    edited  = [d for d in decisions if d["action"] == "edited"]
    skipped = [d for d in decisions if d["action"] == "skip"]

    total = len(decisions)

    # ── Title ────────────────────────────────────────────────────────────────
    lines = [
        f"merge: interactive hunk-by-hunk port {source} → {target}",
        "",
    ]

    # ── Decision summary ─────────────────────────────────────────────────────
    lines += [
        f"Merged via hunk_merger — {total} hunks reviewed across "
        f"{len(file_map)} file(s):",
        f"  {len(taken)} taken  ·  {len(kept)} kept ours  ·  "
        f"{len(edited)} edited  ·  {len(skipped)} skipped",
        "",
    ]

    # ── Group breakdown (hunks reviewed via group picker) ───────────────────
    import re as _re
    group_map: Dict[str, List] = defaultdict(list)
    ungrouped_taken = []
    for d in taken + edited:
        m = _re.search(r'\[group:([^\]]+)\]', d.get("annotation", ""))
        if m:
            group_map[m.group(1)].append(d)
        else:
            ungrouped_taken.append(d)

    if group_map:
        lines.append("Groups ported (reviewed as dependency units):")
        for gsym, gdecs in sorted(group_map.items()):
            gfiles = sorted({d["file"] for d in gdecs})
            lines.append(f"  {gsym}  ({len(gdecs)} hunks in {len(gfiles)} file(s))")
            for gf in gfiles:
                lines.append(f"    {gf}")
        lines.append("")

    # ── Per-file breakdown ───────────────────────────────────────────────────
    lines.append("Per-file decisions:")
    for fp in sorted(file_map.keys()):
        fc = file_map[fp]
        t  = fc.get("theirs", 0)
        o  = fc.get("ours",   0)
        e  = fc.get("edited", 0)
        s  = fc.get("skip",   0)
        parts = []
        if t: parts.append(f"+{t} taken")
        if o: parts.append(f"{o} kept")
        if e: parts.append(f"{e} edited")
        if s: parts.append(f"{s} skipped")
        lines.append(f"  {fp}: {', '.join(parts)}")
    lines.append("")

    # ── Skipped hunks log (so reviewers know what was deferred) ──────────────
    if skipped:
        lines.append("Skipped hunks (deferred / requires follow-up):")
        for d in skipped:
            note = f" — {d['annotation']}" if d.get("annotation") else ""
            lines.append(f"  {d['file']} hunk #{d['hunk_index']+1}{note}")
        lines.append("")

    # ── Git diff stats for staged files ──────────────────────────────────────
    stat = subprocess.run(
        ["git", "diff", "--shortstat", "--cached"],
        cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    if stat:
        lines += [f"Staged: {stat}", ""]

    # ── Footer ───────────────────────────────────────────────────────────────
    lines += [
        f"Source branch : {source}",
        f"Target branch : {target}",
        f"Session date  : {meta.get('last_run', '')}",
        "",
        "[gitship-hunk-merger]",
    ]

    return "\n".join(lines)


# ── Finalize ──────────────────────────────────────────────────────────────────

def finalize(repo: Path, state: Optional[Dict] = None,
             source: str = "", target: str = "",
             is_checkpoint: bool = False):
    """
    Finalize the hunk-merger session:
      1. Show git status — split staged vs unstaged
      2. Offer to stash unstaged changes (keeps staged intact)
      3. Generate commit message from state
      4. Let user edit message, then commit
      5. Optionally pop stash back
    """
    if state is None:
        state = _load_state(repo)
    meta   = state.get("meta", {})
    source = source or meta.get("source", "developer-port")
    target = target or meta.get("target", "development")

    print(f"\n{BOLD}{'═'*64}{R}")
    if is_checkpoint:
        print(f"{BOLD}  CHECKPOINT COMMIT  {CYAN}{source}{R}{BOLD} → {BGREEN}{target}{R}")
        print(f"{DIM}  Session continues after commit — keep reviewing remaining hunks.{R}")
    else:
        print(f"{BOLD}  FINALIZE  {CYAN}{source}{R}{BOLD} → {BGREEN}{target}{R}")
    print(f"{BOLD}{'═'*64}{R}")

    # ── 1. Git status ─────────────────────────────────────────────────────────
    status = subprocess.run(["git", "status", "--short"],
                            cwd=repo, capture_output=True, text=True).stdout
    staged   = [l for l in status.splitlines() if l and l[0] in ('M','A','D','R','C')]
    unstaged = [l for l in status.splitlines() if l and l[1] in ('M','D','?')]

    if not staged:
        print(f"\n{YELLOW}⚠  Nothing staged to commit.{R}")
        print(f"  Run:  git add <files>  then call finalize again.")
        return

    print(f"\n{BOLD}Staged ({len(staged)} files):{R}")
    for l in staged:
        print(f"  {BGREEN}{l}{R}")

    stash_ref = None
    if unstaged:
        print(f"\n{BOLD}Unstaged changes ({len(unstaged)} files):{R}")
        for l in unstaged:
            print(f"  {YELLOW}{l}{R}")
        print(f"\n  These won't be included in the commit.")
        ans = _safe_input("  Stash them now to keep workspace clean? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            msg = f"hunk-merger pre-commit stash ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
            r = subprocess.run(["git", "stash", "push", "--keep-index", "-m", msg],
                               cwd=repo, capture_output=True, text=True)
            if r.returncode == 0:
                stash_ref = r.stdout.strip()
                print(c_ok(f"  ✓ Stashed unstaged changes"))
            else:
                print(c_warn(f"  ⚠  Stash failed: {r.stderr.strip()}"))
                print(c_dim("  Continuing anyway — commit will only include staged files."))

    # ── 2. Generate commit message ────────────────────────────────────────────
    print(f"\n{BOLD}Generating commit message...{R}")

    # Always use the built-in generator; merge_message.py stats are misleading
    # for partial, hunk-based commits.
    msg = _generate_hunk_commit_message(repo, state, source, target)

    # ── 3. Show and offer to edit ─────────────────────────────────────────────
    print(f"\n{BOLD}{'─'*64}{R}")
    print(f"{BOLD}Commit message preview:{R}\n")
    for line in msg.splitlines()[:40]:
        print(f"  {line}")
    if msg.count('\n') > 40:
        print(c_dim(f"  ... ({msg.count(chr(10))-40} more lines)"))
    print(f"{BOLD}{'─'*64}{R}")

    print(f"\n  c. Commit with this message")
    print(f"  e. Edit message first (opens $EDITOR)")
    print(f"  p. Print full message")
    print(f"  0. Cancel (staged changes remain, stash kept)")
    action = _safe_input(f"\n  {BOLD}>{R} ").strip().lower()

    if action in ("p", "print"):
        print("\n" + msg)
        action = _safe_input(f"\n  c=commit  e=edit  0=cancel: ").strip().lower()

    if action in ("e", "edit"):
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                         delete=False, prefix="hm_commit_") as f:
            f.write(msg); tmp = f.name
        subprocess.run([editor, tmp])
        msg = Path(tmp).read_text()
        Path(tmp).unlink(missing_ok=True)
        action = "c"

    if action in ("c", "commit"):
        r = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=repo, capture_output=True, text=True
        )
        if r.returncode == 0:
            print(c_ok(f"\n  ✓ Committed!"))
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                  cwd=repo, capture_output=True, text=True).stdout.strip()
            print(f"  {c_dim(sha)}  {msg.splitlines()[0][:72]}")

            # ── PUSH PROMPT ───────────────────────────────────────────────
            ans_push = _safe_input(f"\n  {BOLD}Push to remote?{R} [y/N]: ").strip().lower()
            if ans_push in ("y", "yes"):
                print(f"  {DIM}Pushing to origin {target}...{R}")
                subprocess.run(["git", "push", "origin", target], cwd=repo)
            # ──────────────────────────────────────────────────────────────

            if is_checkpoint:
                # Keep session files intact — session continues
                print(c_dim("  Session state preserved — keep reviewing remaining hunks."))
            else:
                # Full finalize — clear session files
                sp = _state_path(repo, source, target)
                dp = _diff_path(repo, source, target)
                if sp.exists(): sp.unlink()
                if dp.exists(): dp.unlink()
                print(c_dim("  Session files cleared."))

            # Pop stash if we stashed
            if stash_ref:
                ans2 = _safe_input("\n  Restore stashed changes now? [Y/n]: ").strip().lower()
                if ans2 in ("", "y", "yes"):
                    r2 = subprocess.run(["git", "stash", "pop"],
                                        cwd=repo, capture_output=True, text=True)
                    if r2.returncode == 0:
                        print(c_ok("  ✓ Stash restored"))
                    else:
                        print(c_warn(f"  ⚠  Stash pop failed: {r2.stderr.strip()}"))
                        print(c_dim("  Run:  git stash pop  manually"))
        else:
            print(c_bad(f"\n  ✗ Commit failed:"))
            print(f"  {r.stderr.strip()}")
    else:
        print(c_dim("  Cancelled — staged changes untouched."))
        if stash_ref:
            print(c_dim("  Note: unstaged changes are stashed. Run 'git stash pop' to restore."))

    print()


# ── Report ────────────────────────────────────────────────────────────────────

def show_report(repo: Path):
    state = _load_state(repo)
    if not state["decisions"]:
        print("No decisions yet."); return
    meta = state.get("meta", {})
    print(f"\n{BOLD}Hunk Merger Report{R}")
    if meta:
        print(f"  {meta.get('source','?')} → {meta.get('target','?')}"
              f"  (last: {meta.get('last_run','')})")
    counts: Dict[str, int] = {}
    for d in state["decisions"]:
        a = d.get("action", "?"); counts[a] = counts.get(a, 0) + 1
    cols = {"theirs": BGREEN, "ours": BYELLOW, "edited": BCYAN, "skip": MAGENTA}
    for action, n in sorted(counts.items()):
        print(f"  {cols.get(action,R)}{action:10}{R}  {n}")
    print(f"\n{BOLD}Skipped:{R}")
    _print_skip_log(state)


def reset_state(repo: Path, force: bool = False,
                source: str = "", target: str = ""):
    """
    Clear the hunk_merger state file after confirmation.
    Prints a summary of what is being discarded so the operator knows.
    """
    state = _load_state(repo, source, target)
    meta  = state.get("meta", {})
    source = source or meta.get("source", "")
    target = target or meta.get("target", "")
    sp = _state_path(repo, source, target) if (source and target) else None
    if sp is None or not sp.exists():
        print(c_ok("  ✓ No state file found — nothing to reset.")); return
    decisions = state.get("decisions", [])
    meta      = state.get("meta", {})

    print(f"\n{BOLD}{YELLOW}  RESET HUNK MERGER STATE{R}")
    if meta:
        print(f"  Session: {CYAN}{meta.get('source','?')}{R} → "
              f"{BGREEN}{meta.get('target','?')}{R}  "
              f"{c_dim('last run: ' + meta.get('last_run',''))}")
    if decisions:
        counts: Dict[str, int] = {}
        for d in decisions:
            a = d.get("action","?"); counts[a] = counts.get(a, 0) + 1
        summary = "  ·  ".join(
            f"{c} {a}" for a, c in sorted(counts.items(),
                                           key=lambda x: x[1], reverse=True)
        )
        print(f"  {len(decisions)} decisions will be discarded: {summary}")
    else:
        print(c_dim("  (state file exists but has no decisions)"))

    if not force:
        ans = _safe_input(
            f"\n  {BOLD}Discard all decisions and reset?{R} [y/N]: "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print(c_dim("  Cancelled — state unchanged.")); return

    sp.unlink()
    dp = _diff_path(repo, source, target) if (source and target) else None
    if dp and dp.exists(): dp.unlink()
    print(c_ok("  ✓ Session files deleted — next run will start fresh."))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Interactive hunk-by-hunk branch merger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python hunk_merger.py
              python hunk_merger.py --file src/omnipkg/loader.py
              python hunk_merger.py --resume
              python hunk_merger.py --report
              python hunk_merger.py --finalize
              python hunk_merger.py --groups
        """)
    )
    ap.add_argument("--source",   default="developer-port")
    ap.add_argument("--target",   default="development")
    ap.add_argument("--file",     default=None)
    ap.add_argument("--resume",   action="store_true")
    ap.add_argument("--report",   action="store_true")
    ap.add_argument("--finalize", action="store_true",
                    help="Stage check + generate commit message + commit")
    ap.add_argument("--groups",   action="store_true",
                    help="Show cross-file grouped hunks by shared symbol")
    ap.add_argument("--reset",    action="store_true",
                    help="Discard saved state and start fresh (with confirmation)")
    ap.add_argument("--force-reset", action="store_true",
                    help="Discard saved state without confirmation prompt")
    ap.add_argument("--validate", action="store_true",
                    help="Check whether saved state matches the current diff")
    ap.add_argument("--repo",     default=None)
    args = ap.parse_args()

    repo  = Path(args.repo).resolve() if args.repo else Path.cwd()
    check = repo
    for _ in range(8):
        if (check / ".git").exists(): repo = check; break
        check = check.parent

    if args.report:
        show_report(repo); return
    if args.finalize:
        finalize(repo); return
    if args.groups:
        show_groups(repo); return
    if args.reset or args.force_reset:
        reset_state(repo, force=args.force_reset); return
    if args.validate:
        state = _load_state(repo)
        meta  = state.get("meta", {})
        source = args.source or meta.get("source", "developer-port")
        target = args.target or meta.get("target", "development")
        ff     = args.file  or meta.get("file_filter") or None
        diff_text = _get_diff(repo, source, target, ff)
        files     = parse_diff(diff_text)
        report    = validate_state(repo, state, files)
        print_validation_report(report)
        return

    run_merge(repo=repo, source=args.source, target=args.target,
              file_filter=args.file, resume=args.resume)


if __name__ == "__main__":
    main()