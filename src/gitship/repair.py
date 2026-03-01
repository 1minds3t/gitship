#!/usr/bin/env python3
"""
gitship repair - Diagnose and heal a corrupted Git repository.

This is a focused repair entrypoint that runs the same healing pipeline
as `gitship init` but ONLY the repair stages — it never re-initializes,
nukes history, or makes first commits. Safe to run on any existing repo.

Repair pipeline:
  1. Detect — git status + fsck to classify the damage
  2. Auto-heal — git gc --aggressive (non-destructive, always safe)
  3. Blob repair — re-stage files whose stored blob objects are missing
     (uses working-tree content, falls back to VSCode history or placeholder)
  4. Fetch heal — if a remote exists, fetch to repopulate missing objects
  5. Report — summarize what was fixed and what still needs manual attention
"""

import sys
from pathlib import Path

# ── Reuse init.py's repair engine ───────────────────────────────────────────

try:
    from gitship.init import (
        _fsck_summary,
        _try_gc_recovery,
        _heal_invalid_blobs,
        _parse_invalid_object_paths,
        _stash_working_tree,
        is_git_repo,
        is_corrupted,
    )
    from gitship.gitops import atomic_git_operation
except ImportError:
    # Fallback: load from sibling files directly
    import importlib.util, os

    def _load(name, filename):
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(__file__), filename)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    _init = _load("init", "init.py")
    _fsck_summary            = _init._fsck_summary
    _try_gc_recovery         = _init._try_gc_recovery
    _heal_invalid_blobs      = _init._heal_invalid_blobs
    _parse_invalid_object_paths = _init._parse_invalid_object_paths
    _stash_working_tree      = _init._stash_working_tree
    is_git_repo              = _init.is_git_repo
    is_corrupted             = _init.is_corrupted
    atomic_git_operation     = None


import subprocess


def _run_git(args: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=str(cwd),
        capture_output=True, text=True
    )


# ── Step: fetch from remote to repopulate objects ───────────────────────────

def _fetch_heal(repo_path: Path) -> bool:
    """
    If there's a reachable remote, fetch from it — this repopulates objects
    that exist on the remote but went missing locally (most common corruption cause).
    Returns True if fetch succeeded.
    """
    remotes = _run_git(["remote"], repo_path).stdout.strip().splitlines()
    if not remotes:
        return False

    print(f"\n  Fetching from remotes to repopulate missing objects...")
    any_ok = False
    for remote in remotes:
        print(f"    git fetch {remote} --tags --prune ...", end=" ", flush=True)
        # Don't capture stderr — let SSH auth output through to the terminal
        # so SSH key negotiation works correctly (capturing it can break SSH agent)
        result = subprocess.run(
            ["git", "fetch", remote, "--tags", "--prune"],
            cwd=str(repo_path),
            capture_output=False,   # let SSH output flow to terminal
            stdout=subprocess.DEVNULL,  # suppress normal fetch progress noise
            stderr=None,            # let stderr (SSH, errors) go to terminal
            text=True
        )
        if result.returncode == 0:
            print("✓")
            any_ok = True
        else:
            print(f"✗  (exit code {result.returncode} — see above for details)")
    return any_ok


# ── Step: attempt a test commit to surface invalid-object errors ─────────────

def _probe_commit_errors(repo_path: Path) -> list:
    """
    Do a dry-run index write to find invalid-object errors without
    actually committing anything. Returns list of (path, sha) bad entries.
    """
    # git write-tree will fail and print invalid object errors if blobs are missing
    result = _run_git(["write-tree"], repo_path)
    if result.returncode == 0:
        return []  # Index is clean
    bad = _parse_invalid_object_paths(result.stderr, repo_path)
    return bad


# ── Step: zero-byte object cleanup ──────────────────────────────────────────

def _remove_zero_byte_objects(repo_path: Path) -> int:
    """
    Zero-byte files in .git/objects/ are always corrupt (interrupted write).
    Remove them so git stops tripping over them.
    Returns count removed.
    """
    objects_dir = repo_path / ".git" / "objects"
    removed = 0
    for p in objects_dir.rglob("*"):
        if p.is_file() and p.stat().st_size == 0:
            try:
                p.unlink()
                removed += 1
                print(f"    🗑  Removed zero-byte object: {p.relative_to(repo_path / '.git')}")
            except Exception as e:
                print(f"    ⚠️  Could not remove {p}: {e}")
    return removed


# ── Main repair pipeline ─────────────────────────────────────────────────────

def run_repair(repo_path: Path):
    print("\n" + "=" * 60)
    print("GITSHIP REPAIR")
    print("=" * 60)
    print(f"  Repository: {repo_path}")

    if not is_git_repo(repo_path):
        print("\n  ✗ Not a git repository. Run 'gitship init' to initialize one.")
        sys.exit(1)

    # ── Phase 1: Assess ──────────────────────────────────────────────────────
    print("\n  Phase 1: Assessing repository health...")

    git_ok = not is_corrupted(repo_path)
    has_fsck_errors, fsck_errors = _fsck_summary(repo_path)

    if git_ok and not has_fsck_errors:
        print("\n  ✓ Repository appears healthy — no corruption detected.")
        print("  If you're still seeing errors, describe them and run:")
        print("    git fsck --full")
        return

    if has_fsck_errors:
        print(f"\n  git fsck found {len(fsck_errors)} issue(s):")
        for line in fsck_errors[:10]:
            print(f"    {line}")
        if len(fsck_errors) > 10:
            print(f"    ... ({len(fsck_errors) - 10} more — run 'git fsck --full' to see all)")
    else:
        print("\n  git status is failing but fsck found no errors — likely a lock file or index issue.")

    # ── Phase 2: Stash working tree (safety copy before touching anything) ───
    print("\n  Phase 2: Stashing working tree as safety backup...")
    stash_path = _stash_working_tree(repo_path)
    print(f"  ✓ Working tree backed up → {stash_path}")

    fixed_anything = False

    # ── Phase 3: Remove zero-byte corrupt objects ────────────────────────────
    print("\n  Phase 3: Removing zero-byte corrupt objects...")
    zeroes = _remove_zero_byte_objects(repo_path)
    if zeroes:
        print(f"  ✓ Removed {zeroes} zero-byte object(s)")
        fixed_anything = True
    else:
        print("  ✓ No zero-byte objects found")

    # ── Phase 4: Non-destructive gc recovery ────────────────────────────────
    print("\n  Phase 4: Running git gc --aggressive (non-destructive)...")
    recovered = _try_gc_recovery(repo_path)
    if recovered:
        print("  ✓ Repository recovered via gc — git status now passes!")
        fixed_anything = True
    else:
        print("  ℹ️  gc alone did not fully restore health — continuing...")

    # ── Phase 5: Fetch from remote to repopulate missing objects ─────────────
    print("\n  Phase 5: Fetching from remotes...")
    fetch_ok = _fetch_heal(repo_path)
    if fetch_ok:
        # Re-check after fetch
        still_bad = is_corrupted(repo_path)
        if not still_bad:
            print("\n  ✓ Repository recovered after fetch — git status passes!")
            fixed_anything = True
        else:
            print("  ℹ️  Fetch completed but corruption persists — checking index...")
    else:
        print("  ℹ️  No reachable remotes, or fetch failed — skipping")

    # ── Phase 6: Blob repair (re-stage from working tree) ───────────────────
    print("\n  Phase 6: Checking index for missing blob objects...")
    bad_entries = _probe_commit_errors(repo_path)

    if not bad_entries:
        # Also run a staged diff to catch other index issues
        result = _run_git(["diff", "--cached", "--stat"], repo_path)
        if "unable to read" in result.stderr or "fatal" in result.stderr:
            bad_entries = _parse_invalid_object_paths(result.stderr, repo_path)

    if bad_entries:
        print(f"\n  Found {len(bad_entries)} file(s) with missing blob objects:")
        for p, sha in bad_entries:
            try:
                rel = p.relative_to(repo_path)
            except ValueError:
                rel = p
            print(f"    • {rel}  (blob: {sha[:12]}...)")

        print(f"\n  Auto-healing {len(bad_entries)} file(s) from working tree...")
        healed = _heal_invalid_blobs(repo_path, bad_entries)
        print(f"\n  ✓ Healed {healed}/{len(bad_entries)} file(s)")
        if healed > 0:
            fixed_anything = True
    else:
        print("  ✓ No missing blob objects in index")

    # ── Phase 7: Final health check ──────────────────────────────────────────
    print("\n  Phase 7: Final health check...")
    final_ok = not is_corrupted(repo_path)
    _, final_fsck = _fsck_summary(repo_path)

    # Filter out 'dangling' — those are just unreferenced objects, not harmful
    final_real_errors = [e for e in final_fsck if "dangling" not in e]

    print()
    if final_ok and not final_real_errors:
        print("  ✅ Repository is healthy — all checks pass.")
        if not fixed_anything:
            print("  (Was already healthy, or issue was self-healing)")
    else:
        print("  ⚠️  Some issues remain:")
        for line in final_real_errors[:8]:
            print(f"    {line}")
        if len(final_real_errors) > 8:
            print(f"    ... ({len(final_real_errors) - 8} more)")
        print()
        print("  Next steps:")
        if not final_ok:
            print("    • Your working tree is backed up at:")
            print(f"      {stash_path}")
            print("    • Consider: gitship init  (offers rescue clone + fresh start)")
        print("    • Run: git fsck --full  for full object report")
        print("    • If remote is intact: git clone <url>  into a fresh directory")

    print()
    print(f"  Working tree backup kept at: {stash_path}")
    print("  (Safe to delete once you're confident everything is working)")


# ── Entrypoints ──────────────────────────────────────────────────────────────

def main_with_repo(repo_path: Path):
    """Called by gitship's menu/dispatch system."""
    run_repair(repo_path)


def main():
    """Standalone CLI entrypoint."""
    repo_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    run_repair(repo_path)


if __name__ == "__main__":
    main()
