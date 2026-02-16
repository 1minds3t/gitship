#!/usr/bin/env python3
"""
sync - Unified pull/push/sync operations with conflict resolution.

Handles remote synchronization with automatic conflict detection and resolution.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

try:
    from gitship.gitops import atomic_git_operation, has_ignored_changes
except ImportError:
    atomic_git_operation = None
    has_ignored_changes = None


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


def get_current_branch(repo_path: Path) -> str:
    """Get the currently checked out branch."""
    result = run_git(["branch", "--show-current"], cwd=repo_path)
    return result.stdout.strip()


def get_remote_name(repo_path: Path, branch: Optional[str] = None) -> str:
    """Get the remote name for a branch (usually 'origin')."""
    if branch is None:
        branch = get_current_branch(repo_path)
    
    # Try to get configured remote
    result = run_git(["config", f"branch.{branch}.remote"], cwd=repo_path, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    
    # Default to origin
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
                # Launch interactive resolver
                print("\n🔧 Launching interactive conflict resolver...")
                try:
                    from gitship import resolve_conflicts
                    resolve_conflicts.main()
                    
                    # After resolving, continue
                    if rebase:
                        print("\n✅ Conflicts resolved! Continuing rebase...")
                        continue_result = run_git(["rebase", "--continue"], cwd=repo_path, check=False)
                    else:
                        print("\n✅ Conflicts resolved! Committing merge...")
                        continue_result = run_git(["commit", "--no-edit"], cwd=repo_path, check=False)
                    
                    if continue_result.returncode == 0:
                        print(f"✅ Pull completed successfully!")
                        return True, "Conflicts resolved and pull completed"
                    else:
                        return False, f"Failed to continue: {continue_result.stderr}"
                        
                except Exception as e:
                    print(f"Error launching resolver: {e}")
                    return False, "Conflict resolution failed"
            
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
        # Check for common errors
        if "rejected" in result.stderr.lower():
            print(f"\n❌ Push rejected - remote has changes you don't have")
            print("💡 Try: git pull first, then push again")
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
    if branch is None:
        branch = get_current_branch(repo_path)
    
    if remote is None:
        remote = get_remote_name(repo_path, branch)
    
    print(f"\n🔄 Syncing {branch} with {remote}/{branch}...")
    
    # Pull first
    pull_success, pull_msg = pull_branch(repo_path, branch, remote, rebase)
    if not pull_success:
        return False, f"Pull failed: {pull_msg}"
    
    # Check if we need to push
    result = run_git(["status", "--porcelain", "--branch"], cwd=repo_path)
    if "ahead" in result.stdout:
        # We have local commits to push
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
    # Get local branches
    local_result = run_git(["branch", "--format=%(refname:short)"], cwd=repo_path)
    local_branches = set(local_result.stdout.strip().split('\n'))
    
    # Get remote branches
    remote_result = run_git(["ls-remote", "--heads", remote], cwd=repo_path)
    remote_branches = []
    
    for line in remote_result.stdout.strip().split('\n'):
        if not line:
            continue
        # Parse "hash refs/heads/branch" format
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
            branch = parts[1].replace('refs/heads/', '')
            remote_branches.append(branch)
    
    # Find branches that are remote but not local
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
        print(f"✅ No branches to clean up - local and remote are in sync")
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
    
    # Delete branches from remote
    print(f"\n🗑️  Deleting {len(deleted)} branches from {remote}...")
    
    success_count = 0
    fail_count = 0
    failed_branches = []
    
    for branch in deleted:
        # Use atomic operation if available
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


def get_deleted_branches(repo_path: Path, remote: str = "origin") -> list:
    """
    Find branches that exist on remote but not locally (deleted locally).
    
    Returns:
        List of branch names that were deleted locally
    """
    # Get local branches
    local_result = run_git(["branch", "--format=%(refname:short)"], cwd=repo_path)
    local_branches = set(local_result.stdout.strip().split('\n'))
    
    # Get remote branches
    remote_result = run_git(["branch", "-r", "--format=%(refname:short)"], cwd=repo_path)
    remote_branches = []
    
    for line in remote_result.stdout.strip().split('\n'):
        if not line or 'HEAD' in line:
            continue
        # Strip remote prefix (e.g., "origin/main" -> "main")
        if line.startswith(f"{remote}/"):
            branch = line[len(f"{remote}/"):]
            remote_branches.append(branch)
    
    # Find branches that are remote but not local
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
    
    # Fetch to get latest remote state
    print(f"📡 Fetching from {remote}...")
    fetch_result = run_git(["fetch", remote, "--prune"], cwd=repo_path, check=False)
    if fetch_result.returncode != 0:
        return False, f"Fetch failed: {fetch_result.stderr}"
    
    deleted = get_deleted_branches(repo_path, remote)
    
    if not deleted:
        print(f"✅ No branches to clean up - local and remote are in sync")
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
    
    # Delete branches from remote
    print(f"\n🗑️  Deleting {len(deleted)} branches from {remote}...")
    
    success_count = 0
    fail_count = 0
    failed_branches = []
    
    for branch in deleted:
        # Use atomic operation if available
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
                if '->' not in line:  # Skip HEAD pointer
                    print(f"  {line.strip()}")
            
            branch_name = input("\nEnter branch name: ").strip()
            if '/' in branch_name:
                # User entered remote/branch
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
    
    # Determine command from argv
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