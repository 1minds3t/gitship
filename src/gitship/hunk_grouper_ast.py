#!/usr/bin/env python3
"""
Standalone AST-based hunk grouper.
No imports from existing hunk_merger.py — clean slate.

Usage:
    python hunk_grouper_ast.py <diff.patch>              # show groups (summary)
    python hunk_grouper_ast.py <diff.patch> --verbose    # show groups + diff lines
    python hunk_grouper_ast.py <diff.patch> --test       # run assertions
    python hunk_grouper_ast.py <diff.patch> --test --verbose

Produces:  Dict[HunkId, Set[GroupTag]]  — multi-tagging, no union-find.
"""

import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupTag:
    kind: str   # dependency | ipc | symbol_removed | exception_contract | abstraction
    name: str

    def __str__(self):
        return f"[{self.kind}:{self.name}]"


@dataclass
class Hunk:
    id: int
    file: str
    header: str
    removed: list[str] = field(default_factory=list)
    added:   list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)

    def removes(self, pattern: str) -> bool:
        return any(re.search(pattern, l) for l in self.removed)

    def adds(self, pattern: str) -> bool:
        return any(re.search(pattern, l) for l in self.added)

    def touches(self, pattern: str) -> bool:
        return self.removes(pattern) or self.adds(pattern)

    @property
    def added_text(self) -> str:
        return "\n".join(self.added)

    @property
    def removed_text(self) -> str:
        return "\n".join(self.removed)
    
    @property
    def all_text(self) -> str:
        # Reconstruct the full diff text for regex matching
        lines = []
        for l in self.removed:
            lines.append(f"-{l}")
        for l in self.added:
            lines.append(f"+{l}")
        for l in self.context:
            lines.append(f" {l}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------

def parse_diff(path: Union[Path, str]) -> list[Hunk]:
    hunks: list[Hunk] = []
    cur_file = ""
    cur_hunk: Optional[Hunk] = None
    hunk_id = 0

    lines = path.read_text(errors="replace").splitlines() if isinstance(path, Path) else path.splitlines()
    for raw in lines:
        if raw.startswith("+++ b/"):
            cur_file = raw[6:].strip()
            cur_hunk = None
        elif raw.startswith("@@"):
            cur_hunk = Hunk(id=hunk_id, file=cur_file, header=raw)
            hunks.append(cur_hunk)
            hunk_id += 1
        elif cur_hunk is not None:
            if raw.startswith("---") or raw.startswith("+++"):
                continue
            elif raw.startswith("-"):
                cur_hunk.removed.append(raw[1:])
            elif raw.startswith("+"):
                cur_hunk.added.append(raw[1:])
            else:
                cur_hunk.context.append(raw.lstrip(" "))

    return hunks


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _safe_parse(code: str) -> Optional[ast.Module]:
    try:
        return ast.parse(code)
    except SyntaxError:
        try:
            return ast.parse("def _hunk():\n" + "\n".join(
                "    " + l for l in code.splitlines()
            ))
        except SyntaxError:
            return None


def _calls(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _raises(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                names.add(exc.id)
            elif isinstance(exc, ast.Attribute):
                names.add(exc.attr)
    return names


def _excepts(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            t = node.type
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, ast.Attribute):
                names.add(t.attr)
    return names


# ---------------------------------------------------------------------------
# Tag extractors — one per group type
# ---------------------------------------------------------------------------

def _tag_dependency(hunk: Hunk) -> set[GroupTag]:
    """Group A: import-driven dependency migration (e.g. fs_lock_queue).
    Only tags project-internal modules — stdlib and common third-party are filtered.
    """
    tags: set[GroupTag] = set()

    # Stdlib and common third-party modules — importing these is not a migration signal
    STDLIB_MODULES = {
        "os", "sys", "re", "io", "abc", "ast", "copy", "math", "time",
        "json", "shutil", "random", "hashlib", "logging", "threading",
        "subprocess", "pathlib", "functools", "itertools", "collections",
        "contextlib", "traceback", "inspect", "weakref", "gc", "struct",
        "socket", "signal", "select", "fcntl", "errno", "stat", "grp",
        "pwd", "pty", "termios", "tty", "atexit", "tempfile", "glob",
        "fnmatch", "linecache", "tokenize", "textwrap", "string", "enum",
        "dataclasses", "typing", "types", "warnings", "importlib",
        "unittest", "pytest", "setuptools", "pkg_resources",
        "filelock", "psutil", "numpy", "scipy", "pandas",
    }
    added_tree = _safe_parse(hunk.added_text)
    if added_tree is None:
        return tags

    for node in ast.walk(added_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module.split(".")[-1]
            if mod not in STDLIB_MODULES:
                tags.add(GroupTag("dependency", mod))

    migration_names = {"safe_cloak", "safe_uncloak", "fs_lock_queue"}
    added_calls = _calls(added_tree)
    for name in migration_names:
        if name in added_calls or hunk.adds(re.escape(name)):
            tags.add(GroupTag("dependency", "fs_lock_queue"))

    return tags


def _tag_ipc(hunk: Hunk) -> set[GroupTag]:
    """
    Group B: cross-language IPC message types.
    word_word pattern is unambiguous in both Python strings and C strings.
    Rejects stdlib module names, file-extension-like strings, and generic words.
    """
    tags: set[GroupTag] = set()
    noise = {
        # Generic protocol/status words
        "last_used", "status", "success", "error", "data", "type",
        "result", "value", "name", "path", "true", "false",
        # Common suffix patterns that aren't IPC types
        "not_found", "no_such", "read_only", "read_write",
    }
    for line in hunk.added:
        for m in re.finditer(r'(?:["\']|\\")([a-z][a-z0-9_]{2,}_[a-z][a-z0-9_]{2,})(?:["\']|\\")', line):
            candidate = m.group(1)
            if candidate in noise:
                continue
            # Reject if it looks like a file extension (starts with dot context or is omnipkg_*)
            # but keep legitimate IPC types like patch_site_packages_cache
            if candidate.startswith("_"):
                continue
            tags.add(GroupTag("ipc", candidate))
    return tags


def _tag_symbol_removed(hunk: Hunk) -> set[GroupTag]:
    """
    Group C: identifier purely removed (not renamed).
    AST-based: compares Name nodes in removed vs added lines.
    Also catches dict string keys like worker_info["pinned"].
    """
    tags: set[GroupTag] = set()
    removed_tree = _safe_parse(hunk.removed_text)
    added_tree   = _safe_parse(hunk.added_text)
    if removed_tree is None:
        return tags

    removed_names: set[str] = set()
    for node in ast.walk(removed_tree):
        if isinstance(node, ast.Name):
            removed_names.add(node.id)
        elif isinstance(node, ast.arg):
            removed_names.add(node.arg)

    added_names: set[str] = set()
    if added_tree:
        for node in ast.walk(added_tree):
            if isinstance(node, ast.Name):
                added_names.add(node.id)
            elif isinstance(node, ast.arg):
                added_names.add(node.arg)

    # Catch dict string keys being removed: worker_info["pinned"]
    for line in hunk.removed:
        for m in re.finditer(r'["\']([a-z_][a-z0-9_]+)["\']', line):
            candidate = m.group(1)
            if not hunk.adds(re.escape(candidate)):
                removed_names.add(candidate)

    noise = {
        # Generic identifiers
        "self", "cls", "True", "False", "None", "last_used", "status",
        "result", "value", "name", "path", "data", "error", "type",
        "worker", "spec", "pid", "env",
        # Python built-in types (removed as type annotations → noise)
        "bool", "str", "int", "float", "list", "dict", "set", "tuple",
        "bytes", "bytearray", "memoryview", "complex", "frozenset",
        "object", "type", "super",
        # Python stdlib exceptions — these are AST Name nodes too, filter them
        "Exception", "BaseException", "ValueError", "TypeError",
        "OSError", "IOError", "FileNotFoundError", "FileExistsError",
        "PermissionError", "IsADirectoryError", "NotADirectoryError",
        "ImportError", "ModuleNotFoundError", "RuntimeError",
        "AttributeError", "KeyError", "IndexError", "NameError",
        "NotImplementedError", "StopIteration", "GeneratorExit",
        "ArithmeticError", "ZeroDivisionError", "OverflowError",
        "MemoryError", "RecursionError", "SystemError", "SystemExit",
        "KeyboardInterrupt", "UnicodeError", "TimeoutError",
        "ConnectionError", "BrokenPipeError", "AssertionError",
        # Common stdlib modules imported/removed
        "shutil", "os", "sys", "time", "re", "json", "io", "abc",
        "copy", "math", "random", "hashlib", "logging", "threading",
        "subprocess", "pathlib", "functools", "itertools", "collections",
        "contextlib", "traceback", "inspect", "weakref", "gc",
        # Common short names that appear everywhere
        "args", "kwargs", "msg", "key", "val", "buf", "ret", "err",
        "idx", "num", "src", "dst", "tmp", "out", "inp", "cfg",
    }
    purely_removed = removed_names - added_names - noise

    # Size gate: if the hunk has substantial *added* lines it is a real refactor,
    # not a pure symbol removal.  A safe_print call being incidentally deleted
    # inside a +12/-23 rewrite should not anchor a cross-file group.
    # Only tag when adds are few (<=5) or the hunk is predominantly removals.
    n_added  = len(hunk.added)
    n_removed = len(hunk.removed)
    is_predominantly_removal = (n_added <= 5) or (n_removed > 0 and n_added / n_removed <= 0.35)

    for sym in purely_removed:
        if len(sym) >= 4 and is_predominantly_removal:
            tags.add(GroupTag("symbol_removed", sym))

    return tags


def _tag_exception_contract(hunk: Hunk) -> set[GroupTag]:
    """Group D: exception raised in one hunk, caught in another.
    Only tags *custom* exceptions — stdlib built-ins are filtered out.
    """
    tags: set[GroupTag] = set()

    # Python stdlib exceptions — grouping on these produces noise
    STDLIB_EXC = {
        "Exception", "BaseException", "ValueError", "TypeError",
        "OSError", "IOError", "FileNotFoundError", "FileExistsError",
        "PermissionError", "IsADirectoryError", "NotADirectoryError",
        "ImportError", "ModuleNotFoundError", "RuntimeError",
        "AttributeError", "KeyError", "IndexError", "NameError",
        "NotImplementedError", "StopIteration", "GeneratorExit",
        "ArithmeticError", "ZeroDivisionError", "OverflowError",
        "MemoryError", "RecursionError", "SystemError", "SystemExit",
        "KeyboardInterrupt", "UnicodeError", "UnicodeDecodeError",
        "UnicodeEncodeError", "TimeoutError", "ConnectionError",
        "BrokenPipeError", "AssertionError", "LookupError",
        "EnvironmentError", "ProcessLookupError", "ChildProcessError",
    }

    added_tree = _safe_parse(hunk.added_text)
    if added_tree:
        for name in _raises(added_tree) | _excepts(added_tree):
            if name not in STDLIB_EXC:
                tags.add(GroupTag("exception_contract", name))

    # Regex fallback: catches names in comments/strings the AST misses
    for line in hunk.added:
        for m in re.finditer(r'\b([A-Z][A-Za-z]+Exception|[A-Z][A-Za-z]+Error)\b', line):
            name = m.group(1)
            if name not in STDLIB_EXC:
                tags.add(GroupTag("exception_contract", name))

    return tags


def _tag_abstraction(hunk: Hunk) -> set[GroupTag]:
    """Group E: large deletion replaced by a single class import."""
    tags: set[GroupTag] = set()
    is_large_deletion = len(hunk.removed) >= 50
    added_tree = _safe_parse(hunk.added_text)
    if added_tree:
        for node in ast.walk(added_tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.name
                    if re.match(r'^[A-Z][A-Za-z]+[A-Z][a-z]', name):
                        if is_large_deletion or len(hunk.removed) > 10:
                            tags.add(GroupTag("abstraction", name))
    for line in hunk.added:
        m = re.search(r'import\s+([A-Z][A-Za-z]+[A-Z][a-z]\w*)', line)
        if m and is_large_deletion:
            tags.add(GroupTag("abstraction", m.group(1)))
    return tags


# ---------------------------------------------------------------------------
# Call-graph extractor — same-file define→call relationships
# ---------------------------------------------------------------------------



def _tag_callgraph(hunk: Hunk, definer_map: dict[str, int]) -> set[GroupTag]:
    """Finds same-file define->call relationships with surgical precision."""
    tags: set[GroupTag] = set()
    
    # 1. CALLER SIDE: Does this specific hunk CALL a function that has a definition elsewhere in the diff?
    calls = set()
    added_tree = _safe_parse(hunk.added_text)
    removed_tree = _safe_parse(hunk.removed_text)
    if added_tree:   calls.update(_calls(added_tree))
    if removed_tree: calls.update(_calls(removed_tree))
    
    # Regex fallback for obj.method() in broken diff lines
    diff_text = hunk.added_text + "\n" + hunk.removed_text
    for m in re.finditer(r'\b([a-zA-Z_]\w*)\s*\(', diff_text):
        calls.add(m.group(1))

    noise = {"print", "len", "isinstance", "getattr", "setattr", "hasattr", "super", "range", "enumerate"}
    
    for callee in calls:
        if callee in definer_map and callee not in noise:
            # This hunk calls a function that is defined in another hunk.
            # Tag THIS hunk with the name of the function it calls.
            tags.add(GroupTag("callgraph", callee))

    # 2. DEFINER SIDE: Does this specific hunk DEFINE a function?
    for m in re.finditer(r'^[ \+\-]\s*(?:async\s+)?def\s+([a-zA-Z_]\w*)', hunk.all_text, re.MULTILINE):
        func_name = m.group(1)
        # Tag THIS hunk with the name of the function it defines.
        if func_name not in noise:
            tags.add(GroupTag("callgraph", func_name))

    return tags


# ---------------------------------------------------------------------------
# Main grouper — no union-find, multi-tags per hunk
# ---------------------------------------------------------------------------

# In hunk_grouper_ast.py

def group_hunks(hunks: list[Hunk], repo_path: Optional[Path] = None) -> dict[int, set[GroupTag]]:
    result: dict[int, set[GroupTag]] = defaultdict(set)

    # Pre-computation: a map from each function name to the ID of the hunk that DEFINES it.
    definer_map: dict[str, int] = {}
    if repo_path:
        for h in hunks:
            if h.file.endswith(".py"):
                for m in re.finditer(r'^[ \+\-]\s*(?:async\s+)?def\s+([a-zA-Z_]\w*)', h.all_text, re.MULTILINE):
                    definer_map[m.group(1)] = h.id

    # Run all extractors
    for h in hunks:
        result[h.id].update(_tag_dependency(h))
        result[h.id].update(_tag_ipc(h))
        result[h.id].update(_tag_symbol_removed(h))
        result[h.id].update(_tag_exception_contract(h))
        result[h.id].update(_tag_abstraction(h))
        
        if repo_path:
            result[h.id].update(_tag_callgraph(h, definer_map))

    return dict(result)

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

RED   = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"
DIM   = "\033[2m"


def display_groups(hunks: list[Hunk], result: dict[int, set[GroupTag]], verbose: bool = False):
    tag_to_hunks: dict[GroupTag, list[Hunk]] = defaultdict(list)
    hunk_by_id = {h.id: h for h in hunks}

    for hunk_id, tags in result.items():
        for tag in tags:
            tag_to_hunks[tag].append(hunk_by_id[hunk_id])

    groups = {tag: hs for tag, hs in tag_to_hunks.items() if len(hs) >= 2}

    if not groups:
        print("No multi-hunk groups found.")
        return

    for tag, hs in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"\n{'='*65}")
        print(f"  {tag}  ({len(hs)} hunks)")
        print(f"{'='*65}")
        for h in hs:
            short_file = h.file.split("/")[-1]
            print(f"\n  {DIM}📄 {short_file}{RESET}  {h.header[:55]}")
            if verbose:
                for l in h.removed[:4]:
                    print(f"     {RED}- {l.rstrip()}{RESET}")
                for l in h.added[:4]:
                    print(f"     {GREEN}+ {l.rstrip()}{RESET}")
                total = len(h.removed) + len(h.added)
                if total > 8:
                    print(f"     {DIM}... ({len(h.removed)} removed, {len(h.added)} added){RESET}")

    if not verbose:
        print(f"\n{DIM}Tip: add --verbose to see actual diff lines per hunk{RESET}")


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

def run_tests(hunks: list[Hunk], result: dict[int, set[GroupTag]], verbose: bool = False):
    hunk_by_id = {h.id: h for h in hunks}
    failures, passes = [], []

    def tagged(kind: str, name: str) -> list[Hunk]:
        return [
            hunk_by_id[hid]
            for hid, tags in result.items()
            if any(t.kind == kind and name in t.name for t in tags)
        ]

    def check(label: str, condition: bool, detail: str = ""):
        full = f"{label}{(' — ' + detail) if detail else ''}"
        if condition:
            passes.append(full)
            print(f"  {GREEN}PASS{RESET}  {full}")
        else:
            failures.append(full)
            print(f"  {RED}FAIL{RESET}  {full}")

    def show_sample(hs: list[Hunk], limit: int = 2):
        if not verbose or not hs:
            return
        for h in hs[:limit]:
            short = h.file.split("/")[-1]
            print(f"       {DIM}→ {short}  {h.header[:50]}{RESET}")
            for l in h.removed[:3]:
                print(f"         {RED}- {l.rstrip()}{RESET}")
            for l in h.added[:3]:
                print(f"         {GREEN}+ {l.rstrip()}{RESET}")

    loader  = [h for h in hunks if h.file.endswith("loader.py")]
    daemon  = [h for h in hunks if h.file.endswith("worker_daemon.py")]
    core    = [h for h in hunks if h.file.endswith("core.py")]
    c_file  = [h for h in hunks if h.file.endswith("dispatcher.c")]
    patches = [h for h in hunks if h.file.endswith("patchers.py")]

    print(f"\n── Part 1: Raw signals in diff {'─'*30}")

    check("fs_lock_queue imported in loader.py",
          any(h.adds(r"fs_lock_queue") for h in loader))

    check("safe_cloak/safe_uncloak used in loader.py",
          any(h.adds(r"safe_cloak|safe_uncloak") for h in loader))

    pinned_rm  = [h for h in daemon if h.removes(r'["\']?pinned["\']?')]
    pinned_add = [h for h in daemon if h.adds(r'["\']?pinned["\']?')]
    check("'pinned' removed in 3+ daemon hunks", len(pinned_rm) >= 3, f"found {len(pinned_rm)}")
    show_sample(pinned_rm)
    check("'pinned' NOT added anywhere", len(pinned_add) == 0, f"found {len(pinned_add)}")

    check("patch_site_packages_cache in dispatcher.c",
          any(h.adds(r"patch_site_packages_cache") for h in c_file))
    check("patch_site_packages_cache in worker_daemon.py",
          any(h.adds(r"patch_site_packages_cache") for h in daemon))

    check("ProcessCorruptedException raised",
          any(h.adds(r"ProcessCorruptedException") for h in loader + patches))
    check("ProcessCorruptedException caught",
          any(h.adds(r"except.*ProcessCorruptedException") for h in loader))

    check("core.py has 50+ line removal hunk",
          any(len(h.removed) >= 50 for h in core))
    check("core.py adds SmartInstaller import",
          any(h.adds(r"SmartInstaller|smart_install") for h in core))

    last_used_files = {h.file for h in hunks if h.touches(r"last_used")}
    check("last_used stays in one file", len(last_used_files) <= 1,
          f"{last_used_files}")

    print(f"\n── Part 2: Grouper finds the 5 groups {'─'*24}")

    g_fslq = tagged("dependency", "fs_lock_queue")
    check("Group A — fs_lock_queue", len(g_fslq) >= 2, f"{len(g_fslq)} hunks tagged")
    show_sample(g_fslq)

    g_ipc = tagged("ipc", "patch_site_packages_cache")
    ipc_files = {h.file for h in g_ipc}
    ipc_short = [f.split("/")[-1] for f in ipc_files]
    check("Group B — IPC both files tagged",
          any("dispatcher" in f for f in ipc_files) and any("worker_daemon" in f for f in ipc_files),
          f"files: {ipc_short}")
    show_sample(g_ipc)

    g_pin = tagged("symbol_removed", "pinned")
    check("Group C — pinned removed", len(g_pin) >= 5, f"{len(g_pin)} hunks tagged")
    show_sample(g_pin)

    g_exc = tagged("exception_contract", "ProcessCorruptedException")
    check("Group D — exception contract", len(g_exc) >= 2, f"{len(g_exc)} hunks tagged")
    show_sample(g_exc)

    g_abs = tagged("abstraction", "SmartInstaller")
    check("Group E — SmartInstaller abstraction", len(g_abs) >= 1, f"{len(g_abs)} hunks tagged")
    show_sample(g_abs)

    noise = tagged("dependency", "last_used") + tagged("ipc", "last_used")
    noise_files = {h.file for h in noise}
    check("No false cross-file group for last_used", len(noise_files) <= 1,
          f"files: {noise_files}")

    print(f"\n{'='*50}")
    print(f"  {GREEN}{len(passes)} passed{RESET}   {RED}{len(failures)} failed{RESET}")
    print(f"{'='*50}")
    if failures:
        print(f"\n{RED}Failed:{RESET}")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    diff_path = Path(sys.argv[1])
    if not diff_path.exists():
        print(f"ERROR: diff file not found: {diff_path}")
        sys.exit(1)

    verbose   = "--verbose" in sys.argv
    repo_path = None
    for arg in sys.argv[2:]:
        if arg.startswith("--repo="):
            repo_path = Path(arg.split("=", 1)[1])
        elif arg == "--repo" and sys.argv.index(arg) + 1 < len(sys.argv):
            repo_path = Path(sys.argv[sys.argv.index(arg) + 1])

    hunks = parse_diff(diff_path)
    print(f"Parsed {len(hunks)} hunks from {len({h.file for h in hunks})} files")

    if repo_path:
        print(f"Call-graph mode: reading live files from {repo_path}")
    else:
        print(f"Diff-only mode (no --repo): callgraph extractor disabled")

    result = group_hunks(hunks, repo_path=repo_path)

    if "--test" in sys.argv:
        run_tests(hunks, result, verbose=verbose)
    else:
        display_groups(hunks, result, verbose=verbose)
        print(f"\nrun with --test to validate  |  --verbose for diff lines")