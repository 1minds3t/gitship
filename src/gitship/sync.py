#!/usr/bin/env python3
"""
sync - Unified pull/push/sync operations with conflict resolution.
Handles remote synchronization with automatic conflict detection and resolution.
"""
import subprocess
import sys
import os
from pathlib import Path
from typing import Optional, Tuple

try:
    from gitship.gitops import atomic_git_operation, has_ignored_changes
except ImportError:
    atomic_git_operation = None
    has_ignored_changes = None


def run_git_interactive(args, extra_env: dict = None) -> int:
    """Run a git command interactively with full TTY access.
    Sets GIT_EDITOR=true to prevent editor prompts from blocking.
    Returns exit code."""
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["git"] + args,
        env=env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return result.returncode


def run_git(args, cwd=None, check=True):
    """Run git command and return result."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=check,
            cwd=cwd,
            encoding='utf-8',
            errors='replace'
        )
        return res
    except subprocess.CalledProcessError as e:
        if not check:
            return e
        print(f"Git error: {e.stderr}")
        sys.exit(1)

def get_rebase_branch(repo_path: Path) -> Optional[str]:
    """
    Read the branch being rebased from git's internal state files.
    Returns the branch name (e.g. 'main') or None if not determinable.
    """
    for state_dir in ["rebase-merge", "rebase-apply"]:
        head_name_file = repo_path / ".git" / state_dir / "head-name"
        if head_name_file.exists():
            ref = head_name_file.read_text(encoding='utf-8', errors='replace').strip()
            # "refs/heads/main" -> "main"
            return ref.replace("refs/heads/", "")
    return None


def get_current_branch(repo_path: Path) -> str:
    """
    Get the currently checked out branch.
    Rebase-aware: during a rebase, returns the branch being rebased.
    Exits with a clear error if branch cannot be determined.
    """
    result = run_git(["branch", "--show-current"], cwd=repo_path)
    branch = result.stdout.strip()

    if not branch:
        # Detached HEAD — likely mid-rebase. Try to recover the real branch name.
        rebase_branch = get_rebase_branch(repo_path)
        if rebase_branch:
            return rebase_branch
        # Last resort: parse HEAD symbolically
        result2 = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, check=False)
        fallback = result2.stdout.strip() if result2.returncode == 0 else ""
        if fallback and fallback != "HEAD":
            return fallback
        print("❌ Cannot determine current branch (detached HEAD with no rebase state).")
        print("   Run 'git status' to inspect, then resolve manually.")
        sys.exit(1)

    return branch


def get_remote_name(repo_path: Path, branch: Optional[str] = None) -> str:
    """Get the remote name for a branch (usually 'origin')."""
    if branch is None:
        branch = get_current_branch(repo_path)

    result = run_git(["config", f"branch.{branch}.remote"], cwd=repo_path, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    return "origin"


def has_remote_branch(repo_path: Path, branch: Optional[str] = None, remote: str = "origin") -> bool:
    """Check if a remote branch exists."""
    if branch is None:
        branch = get_current_branch(repo_path)

    result = run_git(["ls-remote", "--heads", remote, branch], cwd=repo_path, check=False)
    return bool(result.stdout.strip())


def is_rebase_in_progress(repo_path: Path) -> bool:
    """Check if there's a rebase in progress."""
    rebase_merge = repo_path / ".git" / "rebase-merge"
    rebase_apply = repo_path / ".git" / "rebase-apply"
    return rebase_merge.exists() or rebase_apply.exists()


def handle_rebase_in_progress(repo_path: Path) -> bool:
    """
    Interactively handle an already-in-progress rebase.
    Returns True if the rebase was successfully completed or aborted (caller can proceed),
    False if user chose to do nothing or something failed.
    """
    branch = get_rebase_branch(repo_path) or "unknown"
    remote = get_remote_name(repo_path, branch)

    print(f"\n⚠️  A rebase is already in progress on branch '{branch}'.")
    print(f"   git status shows unfinished rebase onto {remote}/{branch}.")

    # Check if there are still conflicted files
    conflicted = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
    conflicted_files = [f for f in conflicted.stdout.strip().split('\n') if f]

    if conflicted_files:
        print(f"\n⚠️  There are {len(conflicted_files)} file(s) still with unresolved conflicts:")
        for f in conflicted_files:
            print(f"  - {f}")
        print("\nYou must resolve conflicts before continuing.")
        print("\nWhat would you like to do?")
        print("  1. Resolve conflicts now (launches interactive resolver)")
        print("  2. Abort the rebase entirely (go back to before the rebase started)")
        print("  3. Cancel (do nothing)")
    else:
        print("\n✅ All conflicts are resolved — the rebase just needs to be continued.")
        print("\nWhat would you like to do?")
        print("  1. Continue the rebase (git rebase --continue)")
        print("  2. Abort the rebase entirely (go back to before the rebase started)")
        print("  3. Cancel (do nothing)")

    choice = input("\nChoice (1-3): ").strip()

    if choice == '1':
        if conflicted_files:
            # Launch conflict resolver
            print("\n🔧 Launching conflict resolver...")
            try:
                from gitship import resolve_conflicts
                resolve_conflicts.main()
            except ImportError:
                try:
                    import resolve_conflicts as rc
                    rc.main()
                except ImportError:
                    print("❌ Could not import resolve_conflicts module.")
                    print("   Run 'gitship resolve' manually, then come back and sync.")
                    return False

            # Re-check for conflicts
            conflicted2 = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
            still_conflicted = [f for f in conflicted2.stdout.strip().split('\n') if f]
            if still_conflicted:
                print(f"\n⚠️  Still {len(still_conflicted)} unresolved file(s). Resolve them and try again.")
                return False

        # Continue the rebase — interactive so no editor hangs the shell
        print("\n▶  Running: git rebase --continue")
        rc = run_git_interactive(["rebase", "--continue"])
        if rc == 0:
            print("✅ Rebase completed successfully!")
            return True
        else:
            # Check if more conflicts appeared
            conflicted_check = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
            if conflicted_check.stdout.strip():
                print("\n⚠️  More conflicts arose during rebase continue.")
                return handle_rebase_in_progress(repo_path)
            print(f"❌ git rebase --continue exited with code {rc}")
            return False

    elif choice == '2':
        print("\n⏪  Running: git rebase --abort")
        rc = run_git_interactive(["rebase", "--abort"])
        if rc == 0:
            print(f"✅ Rebase aborted. You are back on branch '{branch}'.")
            return True
        else:
            print(f"❌ git rebase --abort exited with code {rc}")
            return False

    else:
        print("Cancelled. Nothing was changed.")
        return False


def pull_branch(
    repo_path: Path,
    branch: Optional[str] = None,
    remote: Optional[str] = None,
    rebase: bool = True,
    interactive: bool = True
) -> Tuple[bool, str]:
    """
    Pull changes from remote branch.

    Args:
        repo_path: Path to git repository
        branch: Branch to pull (uses current if None)
        remote: Remote name (auto-detects if None)
        rebase: Use rebase instead of merge (default True)
        interactive: Offer conflict resolution on failure

    Returns:
        (success, message)
    """
    # Refuse to pull if a rebase is already in progress
    if is_rebase_in_progress(repo_path):
        print("\n⚠️  Cannot pull: a rebase is already in progress.")
        resolved = handle_rebase_in_progress(repo_path)
        if resolved:
            return True, "Existing rebase completed — you can now sync/push"
        return False, "Rebase in progress — resolve it before pulling"

    if branch is None:
        branch = get_current_branch(repo_path)

    if remote is None:
        remote = get_remote_name(repo_path, branch)

    print(f"\n📥 Pulling {remote}/{branch}...")

    # Check if remote branch exists
    if not has_remote_branch(repo_path, branch, remote):
        return False, f"Remote branch {remote}/{branch} does not exist"

    # Fetch first
    print(f"🔄 Fetching from {remote}...")
    fetch_result = run_git(["fetch", remote], cwd=repo_path, check=False)
    if fetch_result.returncode != 0:
        return False, f"Fetch failed: {fetch_result.stderr}"

    # Build pull command
    pull_cmd = ["pull"]
    if rebase:
        pull_cmd.append("--rebase")
    else:
        pull_cmd.append("--no-rebase")

    pull_cmd.extend([remote, branch])

    # Use atomic operation if available
    if atomic_git_operation:
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=pull_cmd,
            description=f"pull {remote}/{branch}"
        )
    else:
        result = run_git(pull_cmd, cwd=repo_path, check=False)

    if result.returncode == 0:
        print(f"✅ Successfully pulled from {remote}/{branch}")
        if "Already up to date" in result.stdout:
            return True, "Already up to date"
        return True, result.stdout

    # Check for conflicts
    if "CONFLICT" in result.stdout or "conflict" in result.stderr.lower():
        print(f"\n⚠️  Pull has conflicts!")
        print(result.stdout)

        if interactive:
            print("\nWhat would you like to do?")
            if rebase:
                print("  1. Resolve conflicts interactively")
                print("  2. Abort rebase")
                print("  3. Skip this commit")
            else:
                print("  1. Resolve conflicts interactively")
                print("  2. Abort merge")

            choice = input("\nChoice: ").strip()

            if choice == '1':
                print("\n🔧 Launching interactive conflict resolver...")
                try:
                    from gitship import resolve_conflicts
                    resolve_conflicts.main()
                except ImportError:
                    try:
                        import resolve_conflicts as rc
                        rc.main()
                    except ImportError:
                        print("❌ Could not import resolve_conflicts module.")
                        return False, "Conflict resolution module not found"

                # After resolving, continue — interactive to prevent editor hang
                if rebase:
                    print("\n✅ Conflicts resolved! Continuing rebase...")
                    rc = run_git_interactive(["rebase", "--continue"])
                else:
                    print("\n✅ Conflicts resolved! Committing merge...")
                    rc = run_git_interactive(["commit", "--no-edit"])

                if rc == 0:
                    print("✅ Pull completed successfully!")
                    return True, "Conflicts resolved and pull completed"
                else:
                    return False, f"Continue command exited with code {rc}"

            elif choice == '2':
                if rebase:
                    run_git(["rebase", "--abort"], cwd=repo_path)
                    print("✓ Rebase aborted")
                else:
                    run_git(["merge", "--abort"], cwd=repo_path)
                    print("✓ Merge aborted")
                return False, "Pull aborted by user"

            elif choice == '3' and rebase:
                skip_result = run_git(["rebase", "--skip"], cwd=repo_path, check=False)
                if skip_result.returncode == 0:
                    print("✓ Skipped commit")
                    return True, "Skipped conflicting commit"
                else:
                    return False, f"Skip failed: {skip_result.stderr}"
        else:
            return False, "Pull has conflicts (interactive mode disabled)"
    else:
        return False, f"Pull failed: {result.stderr}"


def push_branch(
    repo_path: Path,
    branch: Optional[str] = None,
    remote: Optional[str] = None,
    force: bool = False,
    set_upstream: bool = False
) -> Tuple[bool, str]:
    """
    Push changes to remote branch.

    Args:
        repo_path: Path to git repository
        branch: Branch to push (uses current if None)
        remote: Remote name (auto-detects if None)
        force: Force push (use with caution!)
        set_upstream: Set upstream tracking

    Returns:
        (success, message)
    """
    # Refuse to push if a rebase is still in progress
    if is_rebase_in_progress(repo_path):
        print("\n⚠️  Cannot push: a rebase is still in progress.")
        resolved = handle_rebase_in_progress(repo_path)
        if not resolved:
            return False, "Rebase in progress — resolve it before pushing"
        # Re-derive branch after rebase completion
        branch = None

    if branch is None:
        branch = get_current_branch(repo_path)

    if remote is None:
        remote = get_remote_name(repo_path, branch)

    print(f"\n📤 Pushing to {remote}/{branch}...")

    # Build push command
    push_cmd = ["push"]

    if force:
        print("⚠️  Force push enabled!")
        push_cmd.append("--force-with-lease")  # Safer than --force

    if set_upstream:
        push_cmd.extend(["-u", remote, branch])
    else:
        push_cmd.extend([remote, branch])

    # Use atomic operation if available
    if atomic_git_operation:
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=push_cmd,
            description=f"push to {remote}/{branch}"
        )
    else:
        result = run_git(push_cmd, cwd=repo_path, check=False)

    if result.returncode == 0:
        print(f"✅ Successfully pushed to {remote}/{branch}")
        return True, result.stdout
    else:
        if "rejected" in result.stderr.lower():
            print(f"\n❌ Push rejected — remote has changes you don't have")
            print("💡 Run 'gitship pull' first, then sync again")
            return False, "Push rejected - need to pull first"
        elif "no upstream" in result.stderr.lower():
            print(f"\n💡 No upstream set. Use --set-upstream to push")
            return False, "No upstream branch configured"
        else:
            print(f"❌ Push failed: {result.stderr}")
            return False, result.stderr


def sync_branch(
    repo_path: Path,
    branch: Optional[str] = None,
    remote: Optional[str] = None,
    rebase: bool = True
) -> Tuple[bool, str]:
    """
    Sync with remote: pull then push.

    Args:
        repo_path: Path to git repository
        branch: Branch to sync (uses current if None)
        remote: Remote name (auto-detects if None)
        rebase: Use rebase for pull (default True)

    Returns:
        (success, message)
    """
    # Handle in-progress rebase FIRST before attempting any pull/push
    if is_rebase_in_progress(repo_path):
        print("\n⚠️  Rebase in progress — must resolve this before syncing.")
        resolved = handle_rebase_in_progress(repo_path)
        if not resolved:
            return False, "Rebase in progress — resolve it first"
        # After rebase completes, fall through to push the result

    if branch is None:
        branch = get_current_branch(repo_path)

    if remote is None:
        remote = get_remote_name(repo_path, branch)

    print(f"\n🔄 Syncing {branch} with {remote}/{branch}...")

    # Pull first (only if no rebase was just completed above)
    if not is_rebase_in_progress(repo_path):
        pull_success, pull_msg = pull_branch(repo_path, branch, remote, rebase)
        if not pull_success:
            return False, f"Pull failed: {pull_msg}"

    # Re-read branch in case rebase changed HEAD
    branch = get_current_branch(repo_path)

    # Check if we need to push
    result = run_git(["status", "--porcelain", "--branch"], cwd=repo_path)
    if "ahead" in result.stdout:
        push_success, push_msg = push_branch(repo_path, branch, remote)
        if not push_success:
            return False, f"Push failed: {push_msg}"
        return True, "Synced successfully (pulled and pushed)"
    else:
        return True, "Synced successfully (already up to date)"


def get_deleted_branches(repo_path: Path, remote: str = "origin") -> list:
    """
    Find branches that exist on remote but not locally (deleted locally).

    Returns:
        List of branch names that were deleted locally
    """
    local_result = run_git(["branch", "--format=%(refname:short)"], cwd=repo_path)
    local_branches = set(local_result.stdout.strip().split('\n'))

    remote_result = run_git(["ls-remote", "--heads", remote], cwd=repo_path)
    remote_branches = []

    for line in remote_result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
            branch = parts[1].replace('refs/heads/', '')
            remote_branches.append(branch)

    deleted = [b for b in remote_branches if b not in local_branches]
    return deleted


def sync_deleted_branches(
    repo_path: Path,
    remote: str = "origin",
    interactive: bool = True,
    dry_run: bool = False
) -> Tuple[bool, str]:
    """
    Sync local branch deletions to remote (delete remote branches that were deleted locally).

    Args:
        repo_path: Path to git repository
        remote: Remote name (default "origin")
        interactive: Ask for confirmation before deleting
        dry_run: Show what would be deleted without actually deleting

    Returns:
        (success, message)
    """
    print(f"\n🔍 Checking for branches deleted locally but still on {remote}...")

    deleted = get_deleted_branches(repo_path, remote)

    if not deleted:
        print(f"✅ No branches to clean up — local and remote are in sync")
        return True, "No branches to delete"

    print(f"\n📋 Found {len(deleted)} branch(es) deleted locally but still on {remote}:")
    for i, branch in enumerate(deleted, 1):
        print(f"  {i}. {branch}")

    if dry_run:
        print(f"\n[DRY-RUN] Would delete {len(deleted)} branches from {remote}")
        return True, f"Dry run: {len(deleted)} branches would be deleted"

    if interactive:
        print(f"\n⚠️  This will DELETE these branches from {remote}")
        confirm = input("Delete all listed branches from remote? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("❌ Cancelled")
            return False, "User cancelled"

    print(f"\n🗑️  Deleting {len(deleted)} branches from {remote}...")

    success_count = 0
    fail_count = 0
    failed_branches = []

    for branch in deleted:
        if atomic_git_operation:
            result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", remote, "--delete", branch],
                description=f"delete remote branch '{remote}/{branch}'"
            )
        else:
            result = run_git(["push", remote, "--delete", branch], cwd=repo_path, check=False)

        if result.returncode == 0:
            success_count += 1
            print(f"  ✓ {branch}")
        else:
            fail_count += 1
            failed_branches.append(branch)
            print(f"  ✗ {branch}: {result.stderr.strip()}")

    print(f"\n✅ Deleted {success_count} branches from {remote}")
    if fail_count > 0:
        print(f"⚠️  {fail_count} branches failed: {', '.join(failed_branches)}")
        return False, f"Deleted {success_count}, {fail_count} failed"

    return True, f"Successfully deleted {success_count} branches from remote"


def main_with_repo(repo_path: Path, command: str = "sync"):
    """Interactive sync workflow."""

    # ── Rebase guard: intercept EVERY command if a rebase is stuck ──────────
    if is_rebase_in_progress(repo_path):
        branch = get_rebase_branch(repo_path) or "unknown"
        print(f"\n🚨 gitship detected an unfinished rebase on branch '{branch}'.")
        print(f"   This must be resolved before any {command} operation can proceed.\n")
        resolved = handle_rebase_in_progress(repo_path)
        if not resolved:
            print("\n❌ Operation cancelled — rebase was not resolved.")
            sys.exit(1)
        # If the user only wanted to push/sync after rebase, continue below
        if command == "pull":
            # Pull was what triggered this; we're done
            print("\n✅ Rebase resolved. Nothing more to pull.")
            return

    current_branch = get_current_branch(repo_path)
    remote = get_remote_name(repo_path, current_branch)

    print(f"\n📍 Current branch: {current_branch}")
    print(f"🌐 Remote: {remote}")

    if command == "pull":
        print("\n📥 Pull Options:")
        print("  1. Pull with rebase (recommended)")
        print("  2. Pull with merge")
        print("  3. Pull specific branch")

        choice = input("\nChoice (1-3, default=1): ").strip() or '1'

        if choice == '3':
            branches_result = run_git(["branch", "-r"], cwd=repo_path)
            print("\nRemote branches:")
            for line in branches_result.stdout.strip().split('\n'):
                if '->' not in line:
                    print(f"  {line.strip()}")

            branch_name = input("\nEnter branch name: ").strip()
            if '/' in branch_name:
                parts = branch_name.split('/', 1)
                remote, branch = parts[0], parts[1]
            else:
                branch = branch_name
        else:
            branch = current_branch

        rebase = (choice == '1')
        success, msg = pull_branch(repo_path, branch, remote, rebase)

        if not success:
            sys.exit(1)

    elif command == "push":
        print("\n📤 Push Options:")
        print("  1. Push current branch")
        print("  2. Push with --set-upstream")
        print("  3. Force push (careful!)")

        choice = input("\nChoice (1-3, default=1): ").strip() or '1'

        force = (choice == '3')
        set_upstream = (choice == '2')

        if force:
            confirm = input("\n⚠️  Force push will overwrite remote. Confirm (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("Cancelled")
                return

        success, msg = push_branch(repo_path, current_branch, remote, force, set_upstream)

        if not success:
            sys.exit(1)

    elif command == "sync":
        print("\n🔄 Sync will pull (rebase) then push")

        confirm = input("Continue? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Cancelled")
            return

        success, msg = sync_branch(repo_path, current_branch, remote)

        if not success:
            sys.exit(1)

    elif command == "sync-branches":
        print("\n🗑️  Sync Branch Deletions")
        print("This will delete remote branches that you've already deleted locally")

        success, msg = sync_deleted_branches(repo_path, remote, interactive=True)

        if not success:
            sys.exit(1)


def main():
    """Entry point when called directly."""
    repo_path = Path.cwd()
    if not (repo_path / ".git").exists():
        print("❌ Not in a git repository")
        sys.exit(1)

    command = "sync"  # default
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command not in ["pull", "push", "sync", "sync-branches"]:
            print(f"Unknown command: {command}")
            print("Usage: gitship sync|pull|push|sync-branches")
            sys.exit(1)

    main_with_repo(repo_path, command)


if __name__ == "__main__":
    main()
