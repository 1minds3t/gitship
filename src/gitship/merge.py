#!/usr/bin/env python3
"""
merge.py - Branch merging for gitship.

This module is intentionally thin. All interactive merge logic (remote sync,
smart push, conflict resolution, unrelated-history handling) lives in
branch.merge_branches_interactive() and branch.smart_push_branch().

What lives here:
  - Merge cache: save/restore conflict resolutions across interrupted sessions
  - merge_branch(): non-interactive programmatic merge (used by CLI --branch flag)
  - main_with_repo(): interactive entry point — delegates to branch module
"""

import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

try:
    from gitship.gitops import atomic_git_operation
    from gitship.merge_message import generate_merge_message, amend_last_commit_message
except ImportError:
    atomic_git_operation = None
    generate_merge_message = None
    amend_last_commit_message = None


# =============================================================================
# MERGE CACHE  (save/restore conflict resolutions across sessions)
# =============================================================================

def get_merge_cache_dir(repo_path: Path) -> Path:
    cache_dir = repo_path / ".gitship" / "merge-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def save_merge_state(repo_path: Path, source_branch: str, target_branch: str) -> list:
    """
    Save staged (resolved) files to .gitship/merge-cache so a session that
    was interrupted mid-conflict can pick up where it left off.
    """
    cache_dir = get_merge_cache_dir(repo_path)

    meta_file = cache_dir / "merge-meta.txt"
    with open(meta_file, 'w') as f:
        f.write(f"source={source_branch}\n")
        f.write(f"target={target_branch}\n")

    result = _run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    staged_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]

    files_saved = []
    for filepath in staged_files:
        src = repo_path / filepath
        if src.exists():
            dst = cache_dir / filepath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files_saved.append(filepath)

    if files_saved:
        files_list = cache_dir / "resolved-files.txt"
        with open(files_list, 'w') as f:
            f.write('\n'.join(files_saved))
        print(f"\n💾 Saved {len(files_saved)} resolved file(s) to merge cache")

    return files_saved


def load_merge_state(repo_path: Path) -> Optional[dict]:
    """Load saved merge state if it exists."""
    cache_dir = get_merge_cache_dir(repo_path)
    meta_file = cache_dir / "merge-meta.txt"

    if not meta_file.exists():
        return None

    state = {}
    with open(meta_file, 'r') as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                state[key] = value

    files_list = cache_dir / "resolved-files.txt"
    if files_list.exists():
        with open(files_list, 'r') as f:
            state['resolved_files'] = [l.strip() for l in f if l.strip()]
    else:
        state['resolved_files'] = []

    return state


def restore_merge_state(repo_path: Path) -> bool:
    """Restore saved merge resolutions from cache into working tree + staging."""
    cache_dir = get_merge_cache_dir(repo_path)
    state = load_merge_state(repo_path)

    if not state or not state.get('resolved_files'):
        return False

    print(f"\n♻️  Found cached merge resolutions")
    print(f"   {state.get('source')} -> {state.get('target')}")
    print(f"   {len(state['resolved_files'])} file(s) previously resolved")

    choice = input("\nRestore cached resolutions? (y/n): ").strip().lower()
    if choice != 'y':
        return False

    restored = 0
    for filepath in state['resolved_files']:
        cached = cache_dir / filepath
        target = repo_path / filepath
        if cached.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, target)
            _run_git(["add", filepath], cwd=repo_path)
            restored += 1

    if restored > 0:
        print(f"✅ Restored {restored} resolved file(s)")
        return True

    return False


def clear_merge_cache(repo_path: Path, verbose: bool = False):
    """Clear merge cache after a successful merge."""
    cache_dir = get_merge_cache_dir(repo_path)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        if verbose:
            print("🧹 Cleared merge cache")


# =============================================================================
# INTERNALS
# =============================================================================

def _run_git(args, cwd=None, check=False):
    """Thin subprocess wrapper."""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        check=check,
        cwd=cwd,
        encoding='utf-8',
        errors='replace',
    )


def _is_merge_in_progress(repo_path: Path) -> bool:
    return (repo_path / ".git" / "MERGE_HEAD").exists()


def _get_merging_branch(repo_path: Path) -> Optional[str]:
    """Parse MERGE_MSG to find which branch is being merged."""
    merge_msg = repo_path / ".git" / "MERGE_MSG"
    if merge_msg.exists():
        lines = merge_msg.read_text(encoding='utf-8').splitlines()
        if lines and "Merge branch" in lines[0]:
            parts = lines[0].split("'")
            if len(parts) >= 2:
                return parts[1]
    return None


# =============================================================================
# PUBLIC API
# =============================================================================

def merge_branch(repo_path: Path, source_branch: str,
                 strategy: Optional[str] = None) -> bool:
    """
    Non-interactive programmatic merge — used by the CLI `--branch` flag.
    For the interactive flow, call main_with_repo() instead.

    Returns True on clean merge, False on conflict or failure.
    """
    current = _run_git(["branch", "--show-current"], cwd=repo_path).stdout.strip()
    print(f"\n🔀 Merging '{source_branch}' into '{current}'...")

    cmd = ["merge"]
    if strategy:
        cmd.extend(["-X", strategy])
    cmd.append(source_branch)

    if atomic_git_operation:
        result = atomic_git_operation(repo_path=repo_path, git_command=cmd,
                                      description=f"merge {source_branch}")
    else:
        result = _run_git(cmd, cwd=repo_path)

    if result.returncode == 0:
        print(f"✅ Successfully merged '{source_branch}' into '{current}'")

        if generate_merge_message and amend_last_commit_message:
            print("\n📊 Generating detailed merge message...")
            detailed = generate_merge_message(repo_path, source_branch, current)
            print("\n" + "=" * 70)
            print(detailed)
            print("=" * 70)
            choice = input("\nUse this message? (y/n/e to edit): ").strip().lower()
            if choice == 'y':
                amend_last_commit_message(repo_path, detailed)
                print("✅ Commit message updated")
            elif choice == 'e':
                subprocess.run(["git", "commit", "--amend"], cwd=repo_path)

        clear_merge_cache(repo_path)
        return True

    if "CONFLICT" in result.stdout or "conflict" in result.stderr.lower():
        print(f"\n⚠️  Merge has conflicts.")
        print(result.stdout)
        save_merge_state(repo_path, source_branch, current)
        print("\n💡 Run 'gitship merge' to resolve interactively")
    else:
        print(f"❌ Merge failed: {result.stderr}")

    return False


def main_with_repo(repo_path: Path):
    """
    Interactive merge entry point.

    Handles three situations in order:
      1. A merge is already in progress (conflicts or ready to commit)
      2. There is a cached merge state from a previous interrupted session
      3. Fresh merge: delegate to branch.merge_branches_interactive()
    """
    from gitship.branch import (
        merge_branches_interactive,
        get_current_branch,
        list_branches,
        Colors,
        safe_input,
    )

    # ── 1. Resume an in-progress merge ────────────────────────────────────────
    if _is_merge_in_progress(repo_path):
        merging_branch = _get_merging_branch(repo_path)
        current = get_current_branch(repo_path)

        print(f"\n⚠️  Merge already in progress: '{merging_branch}' → '{current}'")

        conflicts = _run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path)
        conflict_files = [f for f in conflicts.stdout.strip().splitlines() if f]

        if conflict_files:
            print(f"\n❌ Unresolved conflicts ({len(conflict_files)} file(s)):")
            for f in conflict_files:
                print(f"   {Colors.RED}✗{Colors.RESET} {f}")
            print(f"\n  1. Resolve conflicts interactively")
            print(f"  2. Save current state and abort")
            print(f"  3. Abort (discard all resolutions)")
            print(f"  4. Show git status")
            choice = safe_input(f"\nChoice (1-4): ").strip()

            if choice == '1':
                try:
                    from gitship import resolve_conflicts
                    resolve_conflicts.main()
                except Exception as e:
                    print(f"Resolver error: {e}")
                    return

                remaining = _run_git(["diff", "--name-only", "--diff-filter=U"],
                                     cwd=repo_path)
                if remaining.stdout.strip():
                    print("\n⚠️  Conflicts remain — run 'gitship merge' again when ready")
                    save_merge_state(repo_path, merging_branch or "unknown", current)
                    return

                print("\n✅ All conflicts resolved — committing...")
                if atomic_git_operation:
                    res = atomic_git_operation(repo_path=repo_path,
                                               git_command=["commit", "--no-edit"],
                                               description="merge commit")
                else:
                    res = _run_git(["commit", "--no-edit"], cwd=repo_path)

                if res.returncode == 0:
                    print("✅ Merge committed!")
                    clear_merge_cache(repo_path)
                else:
                    print(f"⚠️  Commit failed: {res.stderr.strip()}")
                    save_merge_state(repo_path, merging_branch or "unknown", current)

            elif choice == '2':
                save_merge_state(repo_path, merging_branch or "unknown", current)
                _run_git(["merge", "--abort"], cwd=repo_path)
                print("✓ Merge aborted, resolutions saved to cache")

            elif choice == '3':
                confirm = safe_input("⚠️  Abort and lose resolutions? (y/n): ").strip().lower()
                if confirm == 'y':
                    _run_git(["merge", "--abort"], cwd=repo_path)
                    print("✓ Merge aborted")

            else:
                print(_run_git(["status"], cwd=repo_path).stdout)

        else:
            # No conflicts — just needs a commit
            print("\n✅ No conflicts. Ready to commit the merge.")
            print("  1. Commit now")
            print("  2. Show status first")
            choice = safe_input("\nChoice (1-2): ").strip()

            if choice == '1':
                if atomic_git_operation:
                    res = atomic_git_operation(repo_path=repo_path,
                                               git_command=["commit", "--no-edit"],
                                               description="merge commit")
                else:
                    res = _run_git(["commit", "--no-edit"], cwd=repo_path)

                if res.returncode == 0:
                    print("✅ Merge committed!")
                    clear_merge_cache(repo_path)
                else:
                    print(f"❌ Commit failed: {res.stderr.strip()}")
            else:
                print(_run_git(["status"], cwd=repo_path).stdout)
        return

    # ── 2. Resume from cached state ────────────────────────────────────────────
    cached = load_merge_state(repo_path)
    if cached and cached.get('resolved_files'):
        print(f"\n💾 Found cached merge state:")
        print(f"   {cached.get('source')} → {cached.get('target')}")
        print(f"   {len(cached['resolved_files'])} file(s) previously resolved")
        choice = safe_input("\nRestore and retry? (y/n): ").strip().lower()

        if choice == 'y':
            if restore_merge_state(repo_path):
                source = cached.get('source')
                target = cached.get('target')
                print(f"\n🔀 Committing restored merge: {source} → {target}")
                if atomic_git_operation:
                    res = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["commit", "-m", f"Merge {source} into {target}"],
                        description="merge commit"
                    )
                else:
                    res = _run_git(
                        ["commit", "-m", f"Merge {source} into {target}"],
                        cwd=repo_path
                    )
                if res.returncode == 0:
                    print("✅ Merge completed!")
                    clear_merge_cache(repo_path)
                else:
                    print(f"⚠️  Commit had issues: {res.stderr.strip()}")
                return
        else:
            clear = safe_input("Clear cached state? (y/n): ").strip().lower()
            if clear == 'y':
                clear_merge_cache(repo_path)

    # ── 3. Fresh merge — use branch.merge_branches_interactive() ──────────────
    branches_info = list_branches(repo_path)
    current = get_current_branch(repo_path)
    all_branches = branches_info['local']
    other_branches = [b for b in all_branches if b != current]

    if not other_branches:
        print("\n❌ No other branches available to merge")
        return

    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}MERGE INTO '{current}'{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"\n  Available branches:")
    for i, b in enumerate(other_branches, 1):
        print(f"    {i}. {b}")

    sel = safe_input(
        f"\n{Colors.CYAN}Merge which branch INTO '{current}'? "
        f"(number/name, 'c' to cancel):{Colors.RESET} "
    ).strip()

    if sel.lower() == 'c' or not sel:
        print("Cancelled")
        return

    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(other_branches):
            source = other_branches[idx]
        else:
            print("Invalid selection")
            return
    else:
        if sel not in all_branches:
            print(f"❌ Branch '{sel}' not found")
            return
        source = sel

    merge_branches_interactive(repo_path, source=source, target=current)


def main():
    """Standalone entry point."""
    repo_path = Path.cwd()
    if not (repo_path / ".git").exists():
        print("❌ Not in a git repository")
        sys.exit(1)
    main_with_repo(repo_path)


if __name__ == "__main__":
    main()