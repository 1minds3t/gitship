#!/usr/bin/env python3
"""
branch - Smart branch management for git repositories.

Provides intuitive branch operations including:
- Create new branches
- Switch branches
- Rename branches (including default branch)
- Delete branches
- List branches with status
- Change default branch (local and remote)
- Compare and merge branches simply
"""

import os
import sys
import signal
import subprocess
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class UserCancelled(Exception):
    """Raised on Ctrl+C anywhere in the branch menu — gives a clean exit."""
    pass


def _sigint_handler(sig, frame):
    raise UserCancelled()


def safe_input(prompt: str = "") -> str:
    """
    Drop-in replacement for input() that raises UserCancelled on Ctrl+C
    instead of letting KeyboardInterrupt propagate up as a traceback.
    """
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        raise UserCancelled()

try:
    from gitship.gitops import stash_ignored_changes, restore_latest_stash, atomic_git_operation
except ImportError:
    # Fallback if gitops isn't available yet
    def stash_ignored_changes(*args): return False
    def restore_latest_stash(*args): return False
    def atomic_git_operation(repo_path, git_command, description, **kwargs):
        """Fallback atomic operation without stashing."""
        return subprocess.run(
            ["git"] + git_command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False
        )

# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_CYAN = '\033[96m'


def run_git(args: List[str], repo_path: Path, capture_output: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    """Run a git command in the specified repository."""
    return subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=capture_output,
        text=True,
        check=check,
        encoding='utf-8',
        errors='replace'
    )


def get_current_branch(repo_path: Path) -> Optional[str]:
    """Get the name of the current branch."""
    # Prefer --show-current (Git 2.22+): clean output, empty string on detached HEAD
    result = run_git(["branch", "--show-current"], repo_path)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    
    # Fallback: rev-parse --abbrev-ref (older Git)
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if result.returncode == 0:
        name = result.stdout.strip()
        # Strip spurious 'heads/' prefix that can appear with ambiguous ref names
        if name.startswith("heads/"):
            name = name[len("heads/"):]
        # 'HEAD' means detached HEAD state
        if name == "HEAD":
            return None
        return name
    return None


def get_default_branch(repo_path: Path) -> Optional[str]:
    """Get the default branch name from remote (usually origin)."""
    # Try to get from remote HEAD
    result = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo_path)
    if result.returncode == 0:
        # Output is like "refs/remotes/origin/main"
        ref = result.stdout.strip()
        return ref.split('/')[-1]
    
    # Fallback: check common names
    for branch in ['main', 'master', 'develop']:
        result = run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], repo_path)
        if result.returncode == 0:
            return branch
    
    return None


def list_branches(repo_path: Path) -> Dict[str, List[str]]:
    """List all branches categorized by local/remote."""
    result = run_git(["branch", "-a", "-v"], repo_path)
    
    branches = {
        'local': [],
        'remote': [],
        'current': None
    }
    
    if result.returncode != 0:
        return branches
    
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        
        # Current branch is marked with *
        is_current = line.startswith('*')
        line = line.lstrip('* ')
        
        parts = line.split()
        if not parts:
            continue
        
        branch_name = parts[0]
        
        if branch_name.startswith('remotes/'):
            # Remote branch
            # Skip HEAD reference
            if 'HEAD ->' not in line:
                branches['remote'].append(branch_name)
        else:
            # Local branch
            branches['local'].append(branch_name)
            if is_current:
                branches['current'] = branch_name
    
    return branches


def create_branch(repo_path: Path, branch_name: str, from_ref: Optional[str] = None) -> bool:
    """Create a new branch."""
    args = ["branch", branch_name]
    if from_ref:
        args.append(from_ref)
    
    result = run_git(args, repo_path)
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Created branch '{branch_name}'{Colors.RESET}")
        return True
    else:
        print(f"{Colors.RED}✗ Failed to create branch: {result.stderr.strip()}{Colors.RESET}")
        return False


def switch_branch(repo_path: Path, branch_name: str) -> bool:
    """Switch to a different branch."""
    result = run_git(["checkout", branch_name], repo_path)
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Switched to branch '{branch_name}'{Colors.RESET}")
        return True
    else:
        # Check if it's the "uncommitted changes" error
        error_msg = result.stderr.strip()
        if "would be overwritten by checkout" in error_msg or "Please commit your changes or stash them" in error_msg:
            print(f"{Colors.YELLOW}⚠️  You have uncommitted changes that would be overwritten.{Colors.RESET}")
            print("\nWhat would you like to do?")
            print("  1. Stash changes (save temporarily) and switch")
            print("  2. Commit changes first (recommended)")
            print("  3. Force switch (discard changes - DANGER!)")
            print("  4. Cancel")
            
            choice = safe_input("\nChoice (1-4): ").strip()
            
            if choice == '1':
                # Show exactly what will be stashed before doing it
                dirty = run_git(["status", "--porcelain"], repo_path)
                dirty_files = [l[3:].strip() for l in dirty.stdout.strip().splitlines() if l.strip()]
                print(f"\n📦 Stashing {len(dirty_files)} file(s):")
                for f in dirty_files:
                    print(f"   • {f}")
                stash_msg = "Auto-stash before switching to " + branch_name + ": " + ", ".join(dirty_files)
                stash_result = run_git(["stash", "push", "-m", stash_msg], repo_path)
                if stash_result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Stashed — restore with: git stash apply stash@{{0}}{Colors.RESET}")
                    # Try switch again
                    switch_result = run_git(["checkout", branch_name], repo_path)
                    if switch_result.returncode == 0:
                        print(f"{Colors.GREEN}✓ Switched to branch '{branch_name}'{Colors.RESET}")
                        restore_now = safe_input(f"\n{Colors.CYAN}Restore stashed changes here on '{branch_name}'? (y/n):{Colors.RESET} ").strip().lower()
                        if restore_now == 'y':
                            restore_latest_stash(repo_path)
                            print(f"{Colors.GREEN}✓ Stash restored on '{branch_name}'{Colors.RESET}")
                        else:
                            print(f"{Colors.DIM}   Stash kept — restore later with: git stash apply stash@{{0}}{Colors.RESET}")
                        return True
                    else:
                        print(f"{Colors.RED}✗ Failed to switch: {switch_result.stderr.strip()}{Colors.RESET}")
                        # Pop the stash back since we didn't switch
                        run_git(["stash", "pop"], repo_path)
                        return False
                else:
                    print(f"{Colors.RED}✗ Failed to stash: {stash_result.stderr.strip()}{Colors.RESET}")
                    return False
            
            elif choice == '2':
                print(f"\n💡 Please commit your changes first, then try switching again.")
                print(f"   Run: gitship commit")
                return False
            
            elif choice == '3':
                print(f"\n{Colors.RED}⚠️  WARNING: This will DISCARD all your uncommitted changes!{Colors.RESET}")
                confirm = safe_input("Type 'yes' to confirm: ").strip().lower()
                if confirm == 'yes':
                    force_result = run_git(["checkout", "-f", branch_name], repo_path)
                    if force_result.returncode == 0:
                        print(f"{Colors.GREEN}✓ Force switched to branch '{branch_name}'{Colors.RESET}")
                        print(f"{Colors.RED}✗ Your uncommitted changes have been discarded{Colors.RESET}")
                        return True
                    else:
                        print(f"{Colors.RED}✗ Failed to force switch: {force_result.stderr.strip()}{Colors.RESET}")
                        return False
                else:
                    print("Cancelled.")
                    return False
            
            else:
                print("Cancelled.")
                return False
        else:
            # Some other error
            print(f"{Colors.RED}✗ Failed to switch branch: {error_msg}{Colors.RESET}")
            return False

def confirm_and_delete_branch(repo_path: Path, branch_name: str, current_branch: str, context: str = "") -> bool:
    """
    Two-step confirmed branch deletion used by all delete paths.
    
    Step 1: Show what will be deleted (local + remote status) and ask y/n.
    Step 2: Require typing the branch name to confirm — prevents accidental deletes.
    
    Returns True if deleted, False if cancelled or failed.
    """
    # Guard: refuse to delete the default branch outright
    default_branch = get_default_branch(repo_path)
    if branch_name == default_branch:
        print(f"\n{Colors.BOLD}{Colors.RED}✗ Cannot delete '{branch_name}' — it is the default branch.{Colors.RESET}")
        print(f"\n  To delete it you must first change the default branch:")
        print(f"  {Colors.DIM}Use option 4 (Change default branch) to point the default elsewhere,{Colors.RESET}")
        print(f"  {Colors.DIM}then you can delete '{branch_name}'.{Colors.RESET}")
        offer = safe_input(f"\n{Colors.CYAN}Open 'Change default branch' now? (y/n):{Colors.RESET} ").strip().lower()
        if offer == 'y':
            # List candidates (all local branches except this one)
            branches = list_branches(repo_path)
            candidates = [b for b in branches['local'] if b != branch_name]
            if not candidates:
                print(f"{Colors.YELLOW}No other local branches to set as default.{Colors.RESET}")
                return False
            print(f"\n{Colors.BOLD}Select new default branch:{Colors.RESET}")
            for i, b in enumerate(candidates, 1):
                print(f"  {i}. {b}")
            sel = safe_input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            new_default = None
            if sel.isdigit():
                idx = int(sel) - 1
                if 0 <= idx < len(candidates):
                    new_default = candidates[idx]
            elif sel:
                new_default = sel
            if new_default:
                change_default_branch(repo_path, new_default)
                print(f"\n{Colors.DIM}Default changed. You can now delete '{branch_name}' if you want.{Colors.RESET}")
        return False

    # Check remote existence
    remote_check = run_git(["ls-remote", "--heads", "origin", branch_name], repo_path, check=False)
    has_remote = bool(remote_check.stdout.strip())

    # Guard: can't delete the branch you're on — offer to switch away first
    actual_current = get_current_branch(repo_path)
    if branch_name == actual_current:
        print(f"\n{Colors.YELLOW}⚠️  '{branch_name}' is your currently checked-out branch.{Colors.RESET}")
        print(f"  Git won't allow deleting it while you're on it.")
        
        branches_info = list_branches(repo_path)
        candidates = [b for b in branches_info['local'] if b != branch_name]
        if not candidates:
            print(f"{Colors.RED}  No other branches to switch to. Cannot proceed.{Colors.RESET}")
            return False
        
        print(f"\n  Switch to another branch first:")
        for i, b in enumerate(candidates, 1):
            default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if b == default_branch else ""
            print(f"    {i}. {b}{default_marker}")
        
        sel = safe_input(f"\n{Colors.CYAN}Switch to (number/name, or Enter to cancel):{Colors.RESET} ").strip()
        if not sel:
            print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
            return False
        
        switch_target = None
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(candidates):
                switch_target = candidates[idx]
        else:
            switch_target = sel
        
        if not switch_target:
            print(f"{Colors.RED}Invalid selection.{Colors.RESET}")
            return False
        
        switched = switch_branch(repo_path, switch_target)
        if not switched:
            return False
        # Update current_branch for the rest of this call
        current_branch = switch_target

    # Check for unmerged commits
    unmerged = run_git(["log", "--oneline", f"HEAD..{branch_name}"], repo_path)
    unmerged_count = len([l for l in unmerged.stdout.strip().split('\n') if l])
    
    print(f"\n{Colors.BOLD}{'='*50}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.RED}⚠️  BRANCH DELETION{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*50}{Colors.RESET}")
    if context:
        print(f"{Colors.DIM}{context}{Colors.RESET}")
    print(f"\n  Branch : {Colors.CYAN}{branch_name}{Colors.RESET}")
    print(f"  Local  : {Colors.RED}will be deleted{Colors.RESET}")
    if has_remote:
        print(f"  Remote : {Colors.RED}origin/{branch_name} will also be deleted{Colors.RESET}")
    else:
        print(f"  Remote : {Colors.DIM}not on remote{Colors.RESET}")
    if unmerged_count:
        print(f"\n  {Colors.YELLOW}⚠️  {unmerged_count} commit(s) in this branch are NOT in the current branch.{Colors.RESET}")
        print(f"  {Colors.YELLOW}   These will be permanently lost if you delete.{Colors.RESET}")
    print()
    
    # Step 1: initial y/n
    try:
        step1 = safe_input(f"{Colors.YELLOW}Delete branch '{branch_name}'? (y/n):{Colors.RESET} ").strip().lower()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        print("\nCancelled.")
        return False
    
    if step1 != 'y':
        print(f"{Colors.GREEN}Branch kept.{Colors.RESET}")
        return False
    
    # Step 2: type the name to confirm
    print(f"\n{Colors.BOLD}Confirm by typing the branch name exactly:{Colors.RESET}")
    try:
        typed = safe_input(f"Branch name: ").strip()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        print("\nCancelled.")
        return False
    
    if typed != branch_name:
        print(f"{Colors.RED}✗ Name didn't match. Branch NOT deleted.{Colors.RESET}")
        return False
    
    # Execute local delete (-d first, escalate to -D only if unmerged and user confirmed)
    flag = "-D" if unmerged_count else "-d"
    res = run_git(["branch", flag, branch_name], repo_path)
    if res.returncode != 0:
        print(f"{Colors.RED}✗ Failed to delete local branch: {res.stderr.strip()}{Colors.RESET}")
        return False
    print(f"{Colors.GREEN}✓ Deleted local branch '{branch_name}'{Colors.RESET}")
    
    # Delete remote if present
    if has_remote:
        push_res = run_git(["push", "origin", "--delete", "refs/heads/" + branch_name], repo_path)
        if push_res.returncode == 0:
            print(f"{Colors.GREEN}✓ Deleted remote branch 'origin/{branch_name}'{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠️  Remote delete failed: {push_res.stderr.strip()}{Colors.RESET}")
            print(f"{Colors.DIM}   Run manually: git push origin refs/heads/{branch_name}{Colors.RESET}")
    
    return True


def verify_and_offer_delete(repo_path: Path, source: str, target: str):
    """
    Verify if source changes are present in target (via hash comparison)
    and offer to delete the source branch.
    """
    # Never offer to delete the default branch
    default_branch = get_default_branch(repo_path)
    if source == default_branch:
        print(f"\n{Colors.DIM}ℹ️  '{source}' is the default branch — skipping delete offer.{Colors.RESET}")
        return
    print(f"\n{Colors.BOLD}🔍 Verifying patch integrity...{Colors.RESET}")
    
    # 1. Identify files changed in source (relative to where it branched from target)
    mb_res = run_git(["merge-base", source, target], repo_path)
    if mb_res.returncode != 0:
        # Fallback if no merge base (orphans?)
        print(f"{Colors.YELLOW}⚠ Could not find merge base. Skipping verification.{Colors.RESET}")
        return

    merge_base = mb_res.stdout.strip()
    
    # Get list of files modified in source
    files_cmd = run_git(["diff", "--name-only", f"{merge_base}..{source}"], repo_path)
    files_changed = [f for f in files_cmd.stdout.splitlines() if f]
    
    if not files_changed:
        print(f"{Colors.GREEN}✓ No file changes in source branch.{Colors.RESET}")
    else:
        print(f"{Colors.BOLD}Hash Comparison for changed files:{Colors.RESET}")
        matches = 0
        mismatches = 0
        
        for f in files_changed:
            # Get blob hash in source
            h_src = run_git(["rev-parse", f"{source}:{f}"], repo_path).stdout.strip()
            # Get blob hash in target (HEAD)
            h_tgt = run_git(["rev-parse", f"{target}:{f}"], repo_path).stdout.strip()
            
            # Handle deleted files
            if not h_src and not h_tgt:
                # Both missing? match.
                matches += 1
                continue
            
            if not h_src: h_src = "(deleted)"
            if not h_tgt: h_tgt = "(missing)"
            
            # Shorten hashes for display
            s_src = h_src[:8] if len(h_src)==40 else h_src
            s_tgt = h_tgt[:8] if len(h_tgt)==40 else h_tgt
            
            if h_src == h_tgt:
                print(f"  {Colors.GREEN}✓ {f}: {s_src} == {s_tgt}{Colors.RESET}")
                matches += 1
            else:
                print(f"  {Colors.YELLOW}≠ {f}: {s_src} vs {s_tgt}{Colors.RESET}")
                mismatches += 1
        
        if mismatches > 0:
            print(f"\n{Colors.YELLOW}⚠️  {mismatches} files have different content in {target}.{Colors.RESET}")
            print(f"   (This is normal if {target} has newer changes to these files)")
            # We don't auto-offer delete if hashes differ, to be safe.
            return
        
        print(f"\n{Colors.GREEN}✅ All changed files match exactly in {target}.{Colors.RESET}")

    # Offer delete using two-step confirmation
    print(f"\n{Colors.BOLD}Branch Cleanup:{Colors.RESET}")
    print(f"The branch '{Colors.CYAN}{source}{Colors.RESET}' appears fully synced/redundant.")
    current = get_current_branch(repo_path)
    confirm_and_delete_branch(repo_path, source, current, context=f"All changes from '{source}' are present in '{target}'.")

def rename_branch(repo_path: Path, old_name: str, new_name: str, update_remote: bool = False) -> bool:
    """Rename a branch locally and optionally on remote using atomic operations."""
    current = get_current_branch(repo_path)

    # Detect previous upstream remote before renaming (so we know where to push)
    old_upstream_status = get_branch_upstream_status(repo_path, old_name)
    old_remote = old_upstream_status.get('remote') or 'origin'

    if current == old_name:
        result = run_git(["branch", "-m", new_name], repo_path)
    else:
        result = run_git(["branch", "-m", old_name, new_name], repo_path)
    
    if result.returncode != 0:
        print(f"{Colors.RED}✗ Failed to rename branch: {result.stderr.strip()}{Colors.RESET}")
        return False
    
    print(f"{Colors.GREEN}✓ Renamed local branch '{old_name}' → '{new_name}'{Colors.RESET}")
    
    if update_remote:
        print(f"\n{Colors.CYAN}Updating remote ({old_remote})...{Colors.RESET}")
        # Push new name with upstream tracking set (-u)
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=["push", "-u", old_remote, new_name],
            description=f"push renamed branch '{new_name}' to {old_remote} with upstream tracking"
        )
        
        if result.returncode != 0:
            print(f"{Colors.YELLOW}⚠ Failed to push new branch to remote{Colors.RESET}")
        else:
            print(f"{Colors.GREEN}✓ Pushed '{new_name}' to {old_remote} and set upstream tracking{Colors.RESET}")
            # Delete old remote branch name
            result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", old_remote, "--delete", "refs/heads/" + old_name],
                description=f"delete old remote branch '{old_name}'"
            )
            if result.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted '{old_name}' from {old_remote}{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠ Failed to delete old branch from remote{Colors.RESET}")
    
    return True


def delete_branch(repo_path: Path, branch_name: str, force: bool = False, delete_remote: bool = True) -> bool:
    """Delete a branch locally and optionally on remote using atomic operations."""
    # Check if branch exists on remote
    remote_exists = False
    check_remote = run_git(["ls-remote", "--heads", "origin", branch_name], repo_path)
    if check_remote.returncode == 0 and check_remote.stdout.strip():
        remote_exists = True
    
    # Delete local branch using atomic operation
    flag = "-D" if force else "-d"
    result = atomic_git_operation(
        repo_path=repo_path,
        git_command=["branch", flag, branch_name],
        description=f"delete local branch '{branch_name}'"
    )
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Deleted local branch '{branch_name}'{Colors.RESET}")
        
        # If branch exists on remote and delete_remote is True, delete it there too
        if remote_exists and delete_remote:
            print(f"\n{Colors.CYAN}Deleting from remote...{Colors.RESET}")
            push_result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", "origin", "--delete", "refs/heads/" + branch_name],
                description=f"delete remote branch 'origin/{branch_name}'"
            )
            
            if push_result.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted remote branch 'origin/{branch_name}'{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  Failed to delete remote branch: {push_result.stderr.strip()}{Colors.RESET}")
                print(f"{Colors.DIM}   You can manually delete it with: git push origin refs/heads/{branch_name}{Colors.RESET}")
        elif remote_exists:
            print(f"{Colors.DIM}ℹ️  Remote branch 'origin/{branch_name}' still exists{Colors.RESET}")
            print(f"{Colors.DIM}   Delete it with: git push origin refs/heads/{branch_name}{Colors.RESET}")
        
        return True
    else:
        print(f"{Colors.RED}✗ Failed to delete branch: {result.stderr.strip()}{Colors.RESET}")
        if not force and "not fully merged" in result.stderr:
            print(f"{Colors.YELLOW}💡 Use force delete if you're sure (will lose unmerged changes){Colors.RESET}")
        return False


def change_default_branch(repo_path: Path, new_default: str) -> bool:
    """Change the default branch for the repository using atomic operations."""
    print(f"\n{Colors.BOLD}Changing Default Branch to '{new_default}'{Colors.RESET}")
    print("=" * 60)
    
    result = run_git(["rev-parse", "--verify", f"refs/heads/{new_default}"], repo_path)
    if result.returncode != 0:
        print(f"{Colors.RED}✗ Branch '{new_default}' does not exist locally{Colors.RESET}")
        return False
    
    print(f"\n1. Ensuring '{new_default}' exists on remote...")
    result = atomic_git_operation(
        repo_path=repo_path,
        git_command=["push", "-u", "origin", new_default],
        description=f"push new default branch '{new_default}' to remote"
    )
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Branch pushed/updated on remote{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ Warning: Could not push to remote{Colors.RESET}")
    
    print(f"\n2. Updating remote default branch...")
    result = run_git(["symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{new_default}"], repo_path)
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Updated local tracking of remote default{Colors.RESET}")
    
    print(f"\n{Colors.BRIGHT_CYAN}3. Update default branch on hosting platform:{Colors.RESET}")
    print(f"   {Colors.DIM}GitHub:{Colors.RESET} Settings → Branches → Default branch → Switch to '{new_default}'")
    print(f"   {Colors.DIM}GitLab:{Colors.RESET} Settings → Repository → Default Branch → Select '{new_default}'")
    print(f"   {Colors.DIM}Manual:{Colors.RESET} git remote set-head origin {new_default}")
    
    print(f"\n{Colors.GREEN}✓ Local configuration updated!{Colors.RESET}")
    
    return True

def get_branch_upstream_status(repo_path: Path, branch: str) -> dict:
    """
    Check the upstream tracking status of a branch.
    Returns a dict with keys:
      - 'upstream': str or None  (e.g. 'origin/main', 'lts/py38')
      - 'upstream_gone': bool    (tracked remote no longer exists)
      - 'remote': str or None    (just the remote name part)
      - 'remote_branch': str or None  (just the branch name part)
    """
    result = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"],
        repo_path
    )
    if result.returncode != 0:
        return {'upstream': None, 'upstream_gone': False, 'remote': None, 'remote_branch': None}

    upstream = result.stdout.strip()  # e.g. "origin/main" or "lts/py38"
    
    # Check if the upstream actually exists
    exists = run_git(["rev-parse", "--verify", upstream], repo_path)
    if exists.returncode == 0:
        parts = upstream.split('/', 1)
        return {
            'upstream': upstream,
            'upstream_gone': False,
            'remote': parts[0] if len(parts) == 2 else None,
            'remote_branch': parts[1] if len(parts) == 2 else None,
        }
    
    # Upstream ref is set but doesn't resolve — check if it's flagged as gone
    # by git status --short --branch
    status_res = run_git(["status", "--short", "--branch"], repo_path)
    gone = '[gone]' in status_res.stdout
    parts = upstream.split('/', 1)
    return {
        'upstream': upstream,
        'upstream_gone': gone or True,  # if rev-parse failed it's effectively gone
        'remote': parts[0] if len(parts) == 2 else None,
        'remote_branch': parts[1] if len(parts) == 2 else None,
    }


def get_all_branches_upstream_status(repo_path: Path, local_branches: list) -> dict:
    """Return upstream status for all local branches. {branch: status_dict}"""
    return {b: get_branch_upstream_status(repo_path, b) for b in local_branches}


def search_github_repos(query: str) -> list:
    """
    Search GitHub for repos matching query using the public API (no auth needed for basic search).
    Returns list of dicts with keys: full_name, description, html_url, clone_url, stargazers_count.
    """
    import urllib.request
    import json
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort=stars&per_page=8"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gitship/1.0", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            return data.get("items", [])
    except Exception:
        return []


def add_upstream_remote(repo_path: Path):
    """
    Interactively add a new remote (typically 'upstream' for a forked repo).
    Supports:
      - typing a full URL directly
      - typing just a repo name/owner to search GitHub and pick from results
    After adding, offers to fetch tags and branches from the new remote.
    """
    import urllib.parse

    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}ADD UPSTREAM REMOTE{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")

    # Show existing remotes
    remotes_res = run_git(["remote", "-v"], repo_path)
    if remotes_res.stdout.strip():
        print(f"\n{Colors.BOLD}Current remotes:{Colors.RESET}")
        seen = set()
        for line in remotes_res.stdout.strip().splitlines():
            parts = line.split()
            if parts and parts[0] not in seen:
                seen.add(parts[0])
                url_part = parts[1] if len(parts) > 1 else ""
                print(f"  {Colors.CYAN}{parts[0]}{Colors.RESET}  {Colors.DIM}{url_part}{Colors.RESET}")

    print(f"""
  You can:
    • Type a full URL  (e.g. https://github.com/owner/repo.git)
    • Type a repo name (e.g. filelock  or  tox-dev/filelock)
      → gitship will search GitHub and let you pick the right one
""")

    raw = safe_input(f"{Colors.CYAN}URL or repo name to search:{Colors.RESET} ").strip()
    if not raw:
        print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
        return

    # Determine if it's a URL or a search query
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("git@"):
        clone_url = raw
        # Derive a suggested remote name from the URL
        suggested_name = raw.rstrip("/").split("/")[-1].removesuffix(".git")
    else:
        # Search GitHub
        print(f"\n{Colors.BRIGHT_BLUE}Searching GitHub for '{raw}'...{Colors.RESET}")
        results = search_github_repos(raw)

        if not results:
            print(f"{Colors.YELLOW}No results found. Enter URL directly instead.{Colors.RESET}")
            clone_url = safe_input(f"{Colors.CYAN}Full clone URL:{Colors.RESET} ").strip()
            if not clone_url:
                print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
                return
            suggested_name = clone_url.rstrip("/").split("/")[-1].removesuffix(".git")
        else:
            print(f"\n{Colors.BOLD}GitHub results:{Colors.RESET}")
            for i, repo in enumerate(results, 1):
                stars = repo.get("stargazers_count", 0)
                desc = repo.get("description") or ""
                desc_short = (desc[:55] + "…") if len(desc) > 55 else desc
                star_label = f"{Colors.YELLOW}★{stars}{Colors.RESET}" if stars else ""
                print(f"  {i}. {Colors.CYAN}{repo['full_name']}{Colors.RESET}  {star_label}")
                if desc_short:
                    print(f"     {Colors.DIM}{desc_short}{Colors.RESET}")

            sel = safe_input(f"\n{Colors.CYAN}Select number (or 0 to enter URL manually):{Colors.RESET} ").strip()

            if sel == "0":
                clone_url = safe_input(f"{Colors.CYAN}Full clone URL:{Colors.RESET} ").strip()
                if not clone_url:
                    print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
                    return
                suggested_name = clone_url.rstrip("/").split("/")[-1].removesuffix(".git")
            elif sel.isdigit() and 1 <= int(sel) <= len(results):
                chosen = results[int(sel) - 1]
                clone_url = chosen["clone_url"]
                # Suggest a remote name: 'upstream' if origin exists, else owner
                existing_remotes = [r.strip() for r in run_git(["remote"], repo_path).stdout.strip().splitlines()]
                if "upstream" not in existing_remotes:
                    suggested_name = "upstream"
                else:
                    suggested_name = chosen["full_name"].split("/")[0]
                print(f"\n{Colors.GREEN}Selected:{Colors.RESET} {chosen['full_name']}")
                print(f"{Colors.DIM}  {clone_url}{Colors.RESET}")

                # Confirm before continuing
                confirm = safe_input(f"\n{Colors.YELLOW}Is this the right repo? (y/n):{Colors.RESET} ").strip().lower()
                if confirm != "y":
                    print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
                    return
            else:
                print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                return

    # Ask for remote name
    remote_name = safe_input(f"\n{Colors.CYAN}Remote name (default={suggested_name}):{Colors.RESET} ").strip() or suggested_name

    # Check if name already exists
    existing_remotes = [r.strip() for r in run_git(["remote"], repo_path).stdout.strip().splitlines()]
    if remote_name in existing_remotes:
        print(f"\n{Colors.YELLOW}Remote '{remote_name}' already exists:{Colors.RESET}")
        existing_url_res = run_git(["remote", "get-url", remote_name], repo_path)
        print(f"  Current URL: {Colors.DIM}{existing_url_res.stdout.strip()}{Colors.RESET}")
        overwrite = safe_input(f"{Colors.YELLOW}Replace with new URL? (y/n):{Colors.RESET} ").strip().lower()
        if overwrite == "y":
            run_git(["remote", "set-url", remote_name, clone_url], repo_path)
            print(f"{Colors.GREEN}✓ Updated URL for remote '{remote_name}'{Colors.RESET}")
        else:
            print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
            return
    else:
        result = run_git(["remote", "add", remote_name, clone_url], repo_path)
        if result.returncode != 0:
            print(f"{Colors.RED}✗ Failed to add remote: {result.stderr.strip()}{Colors.RESET}")
            return
        print(f"{Colors.GREEN}✓ Added remote '{remote_name}' → {clone_url}{Colors.RESET}")

    # Offer to fetch tags and branches
    fetch_choice = safe_input(f"\n{Colors.CYAN}Fetch branches & tags from '{remote_name}' now? (y/n):{Colors.RESET} ").strip().lower()
    if fetch_choice == "y":
        print(f"\n{Colors.BRIGHT_BLUE}Fetching from {remote_name}...{Colors.RESET}")
        result = run_git(["fetch", remote_name, "--tags", "--prune"], repo_path)
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Fetched all branches and tags from '{remote_name}'{Colors.RESET}")
            # Show what we got
            tags_res = run_git(["tag", "--list", "--sort=-version:refname"], repo_path)
            tags = [t.strip() for t in tags_res.stdout.strip().splitlines() if t.strip()]
            if tags:
                preview = ", ".join(tags[:8])
                more = f" (+{len(tags)-8} more)" if len(tags) > 8 else ""
                print(f"  Tags available: {Colors.DIM}{preview}{more}{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ Fetch had issues: {result.stderr.strip()}{Colors.RESET}")

    print(f"\n{Colors.DIM}Tip: To pull their changes later: git fetch {remote_name} --tags{Colors.RESET}")


def fix_upstream_tracking(repo_path: Path, branch: str, upstream_status: dict):
    """
    Interactive fix for a branch whose upstream is gone or misconfigured.
    Options:
      1. Point to a different remote/branch
      2. Unset upstream (clean local-only branch)
      3. Push to a remote to create a new upstream
      4. Cancel
    """
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}FIX UPSTREAM TRACKING{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    
    old_upstream = upstream_status.get('upstream', '(none)')
    print(f"\n  Branch  : {Colors.CYAN}{branch}{Colors.RESET}")
    if upstream_status['upstream_gone']:
        print(f"  Problem : {Colors.RED}Upstream '{old_upstream}' no longer exists (remote deleted or renamed){Colors.RESET}")
    else:
        print(f"  Upstream: {Colors.YELLOW}{old_upstream}{Colors.RESET}")
    
    # Get available remotes
    remotes_res = run_git(["remote"], repo_path)
    remotes = [r.strip() for r in remotes_res.stdout.strip().splitlines() if r.strip()]
    
    print(f"\n  Available remotes: {Colors.DIM}{', '.join(remotes) if remotes else '(none)'}{Colors.RESET}")
    print()
    print(f"  {Colors.BOLD}What would you like to do?{Colors.RESET}")
    print(f"  1. Set upstream to a different remote branch")
    print(f"  2. Unset upstream  (make branch local-only, no tracking)")
    print(f"  3. Push branch to a remote  (creates new upstream)")
    print(f"  4. Cancel")
    print()
    
    choice = safe_input(f"{Colors.CYAN}Choose (1-4):{Colors.RESET} ").strip()
    
    if choice == '1':
        # Let user pick a remote and branch name
        if not remotes:
            print(f"{Colors.RED}No remotes configured.{Colors.RESET}")
            return
        
        print(f"\n{Colors.BOLD}Available remotes:{Colors.RESET}")
        for i, r in enumerate(remotes, 1):
            print(f"  {i}. {r}")
        
        remote_sel = safe_input(f"\n{Colors.CYAN}Select remote (number or name, default={remotes[0]}):{Colors.RESET} ").strip()
        if remote_sel.isdigit():
            idx = int(remote_sel) - 1
            remote = remotes[idx] if 0 <= idx < len(remotes) else remotes[0]
        elif remote_sel:
            remote = remote_sel
        else:
            remote = remotes[0]
        
        # Show what branches exist on that remote
        print(f"\n{Colors.DIM}Fetching branch list from {remote}...{Colors.RESET}")
        ls_res = run_git(["ls-remote", "--heads", remote], repo_path)
        remote_branches = []
        if ls_res.returncode == 0:
            for line in ls_res.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                    remote_branches.append(parts[1].replace('refs/heads/', ''))
        
        if remote_branches:
            print(f"\n{Colors.BOLD}Branches on {remote}:{Colors.RESET}")
            for i, rb in enumerate(remote_branches, 1):
                same = f" {Colors.DIM}(same name){Colors.RESET}" if rb == branch else ""
                print(f"  {i}. {rb}{same}")
            
            rb_sel = safe_input(f"\n{Colors.CYAN}Select branch (number/name, default={branch}):{Colors.RESET} ").strip()
            if rb_sel.isdigit():
                idx = int(rb_sel) - 1
                remote_branch = remote_branches[idx] if 0 <= idx < len(remote_branches) else branch
            elif rb_sel:
                remote_branch = rb_sel
            else:
                remote_branch = branch
        else:
            remote_branch = safe_input(f"{Colors.CYAN}Remote branch name (default={branch}):{Colors.RESET} ").strip() or branch
        
        new_upstream = f"{remote}/{remote_branch}"
        result = run_git(["branch", f"--set-upstream-to={new_upstream}", branch], repo_path)
        if result.returncode == 0:
            print(f"\n{Colors.GREEN}✓ Upstream for '{branch}' set to '{new_upstream}'  {Colors.RESET}")
            # Fetch so the ref actually exists locally
            print(f"{Colors.DIM}  Fetching {remote}...{Colors.RESET}")
            run_git(["fetch", remote], repo_path)
        else:
            print(f"\n{Colors.RED}✗ Failed: {result.stderr.strip()}{Colors.RESET}")
            # Maybe the remote ref doesn't exist yet — offer to just set it anyway
            force = safe_input(f"{Colors.YELLOW}Set tracking anyway even if remote branch doesn't exist yet? (y/n):{Colors.RESET} ").strip().lower()
            if force == 'y':
                # Manually write the config
                run_git(["config", f"branch.{branch}.remote", remote], repo_path)
                run_git(["config", f"branch.{branch}.merge", f"refs/heads/{remote_branch}"], repo_path)
                print(f"{Colors.GREEN}✓ Tracking config written for '{branch}' → {remote}/{remote_branch}{Colors.RESET}")
    
    elif choice == '2':
        result = run_git(["branch", "--unset-upstream", branch], repo_path)
        if result.returncode == 0:
            print(f"\n{Colors.GREEN}✓ Upstream unset for '{branch}'. Branch is now local-only.{Colors.RESET}")
        else:
            print(f"\n{Colors.RED}✗ Failed: {result.stderr.strip()}{Colors.RESET}")
    
    elif choice == '3':
        if not remotes:
            print(f"{Colors.RED}No remotes configured.{Colors.RESET}")
            return
        
        print(f"\n{Colors.BOLD}Available remotes:{Colors.RESET}")
        for i, r in enumerate(remotes, 1):
            print(f"  {i}. {r}")
        
        remote_sel = safe_input(f"\n{Colors.CYAN}Push to remote (default=origin):{Colors.RESET} ").strip()
        if remote_sel.isdigit():
            idx = int(remote_sel) - 1
            remote = remotes[idx] if 0 <= idx < len(remotes) else 'origin'
        elif remote_sel:
            remote = remote_sel
        else:
            remote = 'origin' if 'origin' in remotes else remotes[0]
        
        remote_branch = safe_input(f"{Colors.CYAN}Remote branch name (default={branch}):{Colors.RESET} ").strip() or branch
        
        print(f"\n{Colors.CYAN}Pushing '{branch}' to {remote}/{remote_branch} with upstream tracking...{Colors.RESET}")
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=["push", "-u", remote, f"{branch}:{remote_branch}"],
            description=f"push {branch} to {remote}/{remote_branch} with -u tracking"
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Pushed and upstream set to '{remote}/{remote_branch}'  {Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Push failed: {result.stderr.strip()}{Colors.RESET}")
    
    else:
        print(f"{Colors.DIM}Cancelled.{Colors.RESET}")


def ensure_clean_git_state(repo_path: Path) -> bool:
    """
    Check for interrupted git operations (merge, cherry-pick) and offer to abort.
    Returns True if clean (or cleaned), False if user wants to stay in dirty state.
    """
    git_dir = repo_path / ".git"
    
    # Map marker files to descriptions and abort commands
    states = [
        ("CHERRY_PICK_HEAD", "CHERRY-PICK", ["cherry-pick", "--abort"]),
        ("MERGE_HEAD", "MERGE", ["merge", "--abort"]),
        ("REVERT_HEAD", "REVERT", ["revert", "--abort"]),
        ("rebase-merge", "REBASE", ["rebase", "--abort"]),
        ("rebase-apply", "REBASE", ["rebase", "--abort"])
    ]
    
    for marker, name, abort_cmd in states:
        if (git_dir / marker).exists():
            print(f"\n{Colors.RED}⚠️  Repository is in the middle of a {name}!{Colors.RESET}")
            print(f"   This prevents switching branches or starting new merges.")
            
            choice = safe_input(f"\n{Colors.YELLOW}Abort the stuck {name} and reset to clean state? (y/n):{Colors.RESET} ").strip().lower()
            if choice == 'y':
                res = run_git(abort_cmd, repo_path)
                if res.returncode == 0:
                    print(f"{Colors.GREEN}✓ {name} aborted. State cleaned.{Colors.RESET}")
                    return True
                else:
                    print(f"{Colors.RED}✗ Failed to abort: {res.stderr.strip()}{Colors.RESET}")
                    return False
            return False
            
    return True


def has_common_ancestor(repo_path: Path, branch1: str, branch2: str) -> bool:
    """Return True if branch1 and branch2 share any common commit ancestor."""
    result = run_git(["merge-base", branch1, branch2], repo_path)
    return result.returncode == 0 and bool(result.stdout.strip())


def handle_unrelated_histories(repo_path: Path, source: str, target: str):
    """
    Called when source and target share no common ancestor (unrelated histories).
    This is the 'gitship init created master fresh while main already existed on
    remote' scenario. Presents three clean options instead of a confusing error.
    """
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.YELLOW}⚠  UNRELATED HISTORIES DETECTED{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"""
  {Colors.CYAN}{source}{Colors.RESET} and {Colors.CYAN}{target}{Colors.RESET} have no common commit ancestor.
  This usually means one branch was created fresh (e.g. via
  'git init') while the other already had independent history
  (e.g. an existing remote branch).

  Standard merge is blocked by git to prevent accidentally
  smashing two unrelated trees together.
""")

    # Show what's in each branch so user knows what they're choosing between
    src_log = run_git(["log", "--oneline", "-5", source], repo_path).stdout.strip()
    tgt_log = run_git(["log", "--oneline", "-5", target], repo_path).stdout.strip()

    print(f"  {Colors.BOLD}Last commits in {Colors.CYAN}{source}{Colors.RESET}{Colors.BOLD}:{Colors.RESET}")
    for line in (src_log.splitlines() if src_log else ["(no commits)"]):
        print(f"    {Colors.GREEN}+{Colors.RESET} {line}")

    print(f"\n  {Colors.BOLD}Last commits in {Colors.CYAN}{target}{Colors.RESET}{Colors.BOLD}:{Colors.RESET}")
    for line in (tgt_log.splitlines() if tgt_log else ["(no commits)"]):
        print(f"    {Colors.DIM}·{Colors.RESET} {line}")

    print(f"""
  {Colors.BOLD}OPTIONS:{Colors.RESET}

  {Colors.GREEN}1. Rebase {source} onto {target}{Colors.RESET} {Colors.DIM}(recommended){Colors.RESET}
     Replays your {source} commits on top of {target}'s history.
     Result: linear history, {source} commits land cleanly on {target}.
     Best when: {source} has new work you want to land on {target}.

  {Colors.YELLOW}2. Force merge (--allow-unrelated-histories){Colors.RESET}
     Joins both histories with a merge commit.
     Result: both histories preserved, connected by a merge commit.
     Best when: you genuinely need content from both independent trees.

  {Colors.CYAN}3. Push {source} as a separate branch → open PR on GitHub{Colors.RESET}
     Leaves both branches independent. Opens a PR so you can
     review and merge through GitHub's interface instead.
     Best when: you're not sure and want a second look first.

  {Colors.DIM}0. Cancel{Colors.RESET}
""")

    choice = safe_input(f"{Colors.BLUE}Choice (0-3):{Colors.RESET} ").strip()

    if choice == "0":
        print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
        return

    elif choice == "1":
        # Rebase source onto target
        print(f"\n{Colors.BOLD}📐 REBASE: {source} onto {target}{Colors.RESET}")
        print(f"  This will:")
        print(f"    1. Switch to {Colors.CYAN}{source}{Colors.RESET}")
        print(f"    2. Rebase it onto {Colors.CYAN}{target}{Colors.RESET}")
        print(f"    3. Switch to {Colors.CYAN}{target}{Colors.RESET} and fast-forward merge")
        print(f"\n  {Colors.YELLOW}Note: rewrites {source} commit SHAs (normal for rebase){Colors.RESET}")
        confirm = safe_input(f"\n  {Colors.YELLOW}Continue? (y/n):{Colors.RESET} ").strip().lower()
        if confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
            return

        stashed = stash_ignored_changes(repo_path, f"Before rebase {source} onto {target}")

        # Switch to source
        print(f"\n  [1/3] Switching to {source}...")
        res = run_git(["checkout", source], repo_path)
        if res.returncode != 0:
            print(f"{Colors.RED}✗ Could not switch to {source}: {res.stderr.strip()}{Colors.RESET}")
            if stashed:
                restore_latest_stash(repo_path)
            return

        # Rebase onto target
        print(f"  [2/3] Rebasing {source} onto {target}...")
        res = run_git(["rebase", target], repo_path)
        if res.returncode != 0:
            print(f"{Colors.RED}✗ Rebase failed:{Colors.RESET}")
            print(f"  {res.stderr.strip() or res.stdout.strip()}")
            run_git(["rebase", "--abort"], repo_path)
            print(f"  Rebase aborted — branch restored to original state.")
            if stashed:
                restore_latest_stash(repo_path)
            return

        # Fast-forward target
        print(f"  [3/3] Fast-forwarding {target}...")
        res = run_git(["checkout", target], repo_path)
        if res.returncode == 0:
            res = run_git(["merge", "--ff-only", source], repo_path)
            if res.returncode == 0:
                print(f"\n{Colors.GREEN}✅ Rebase complete! {target} is now up to date with {source}'s commits.{Colors.RESET}")
                if stashed:
                    restore_latest_stash(repo_path)
                _offer_push_after_unrelated(repo_path, target, source)
            else:
                print(f"{Colors.RED}✗ Fast-forward failed: {res.stderr.strip()}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Could not switch to {target}{Colors.RESET}")

    elif choice == "2":
        # Force merge with --allow-unrelated-histories
        print(f"\n{Colors.BOLD}🔀 FORCE MERGE: {source} → {target} (unrelated histories){Colors.RESET}")
        print(f"  {Colors.YELLOW}This joins two independent trees into one with a merge commit.{Colors.RESET}")
        print(f"  Both histories will be preserved.")
        confirm = safe_input(f"\n  {Colors.YELLOW}Continue? (y/n):{Colors.RESET} ").strip().lower()
        if confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
            return

        stashed = stash_ignored_changes(repo_path, f"Before force-merge {source} into {target}")

        print(f"\n  [1/2] Switching to {target}...")
        res = run_git(["checkout", target], repo_path)
        if res.returncode != 0:
            print(f"{Colors.RED}✗ Could not switch to {target}: {res.stderr.strip()}{Colors.RESET}")
            if stashed:
                restore_latest_stash(repo_path)
            return

        print(f"  [2/2] Merging {source} (allowing unrelated histories)...")
        msg = f"Merge unrelated branch '{source}' into '{target}'"
        res = run_git(["merge", "--allow-unrelated-histories", "--no-ff", "-m", msg, source], repo_path)

        if res.returncode == 0:
            print(f"\n{Colors.GREEN}✅ Merge complete!{Colors.RESET}")
            if stashed:
                restore_latest_stash(repo_path)
            _offer_push_after_unrelated(repo_path, target, source)
        else:
            # Conflicts during unrelated merge are common (both have README, .gitignore etc.)
            print(f"\n{Colors.YELLOW}⚠️  Merge has conflicts (common when joining unrelated trees).{Colors.RESET}")
            print(f"  Files that need manual resolution:")
            conflicts = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path)
            for f in conflicts.stdout.strip().splitlines():
                print(f"    {Colors.RED}✗{Colors.RESET} {f}")
            print(f"""
  To resolve:
    1. Edit the conflicted files (look for <<<<<<< markers)
    2. {Colors.CYAN}git add .{Colors.RESET}
    3. {Colors.CYAN}git commit{Colors.RESET}

  To abort and go back:
    {Colors.CYAN}git merge --abort{Colors.RESET}
""")

    elif choice == "3":
        # Push source as separate branch, show PR link
        print(f"\n{Colors.BOLD}🚀 PUSH {source} → remote as separate branch{Colors.RESET}")
        confirm = safe_input(f"  Push '{source}' to origin/{source}? (y/n): ").strip().lower()
        if confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
            return

        res = run_git(["push", "-u", "origin", source], repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✓ Pushed '{source}' to remote.{Colors.RESET}")
            # Try to infer GitHub PR URL from remote
            remote_url = run_git(["remote", "get-url", "origin"], repo_path).stdout.strip()
            pr_url = _github_pr_url(remote_url, source, target)
            if pr_url:
                print(f"\n  {Colors.CYAN}Open a PR here:{Colors.RESET}")
                print(f"  {Colors.BRIGHT_BLUE}{pr_url}{Colors.RESET}")
            else:
                print(f"\n  Open a Pull Request on your git host to merge '{source}' into '{target}'.")
        else:
            print(f"{Colors.RED}✗ Push failed: {res.stderr.strip()}{Colors.RESET}")

    else:
        print(f"{Colors.RED}Invalid choice.{Colors.RESET}")


def _offer_push_after_unrelated(repo_path: Path, target: str, source: str):
    """After a successful unrelated-history resolution, offer to push and clean up."""
    push = safe_input(f"\n{Colors.CYAN}🚀 Push {target} to remote now? (y/n):{Colors.RESET} ").strip().lower()
    if push == 'y':
        res = run_git(["push", "origin", target], repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✓ Pushed origin/{target}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Push failed: {res.stderr.strip()}{Colors.RESET}")

    # Offer to delete the now-redundant source branch
    delete = safe_input(f"\n  Delete local branch '{source}' now? (y/n): ").strip().lower()
    if delete == 'y':
        current = get_current_branch(repo_path)
        if current == source:
            run_git(["checkout", target], repo_path)
        res = run_git(["branch", "-d", source], repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✓ Deleted local branch '{source}'{Colors.RESET}")
        else:
            # -d refuses if not fully merged; offer -D
            res2 = run_git(["branch", "-D", source], repo_path)
            if res2.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted local branch '{source}' (force){Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  Could not delete '{source}': {res2.stderr.strip()}{Colors.RESET}")


def _github_pr_url(remote_url: str, source: str, target: str) -> str:
    """
    Try to construct a GitHub PR URL from a remote URL.
    Handles https://github.com/... and git@github.com:... formats.
    Returns empty string if not a GitHub remote.
    """
    import re
    # SSH: git@github.com:owner/repo.git or git@github-alias:owner/repo.git
    ssh_match = re.match(r"git@[^:]+:([^/]+/[^.]+)(?:\.git)?$", remote_url)
    if ssh_match:
        return f"https://github.com/{ssh_match.group(1)}/compare/{target}...{source}?expand=1"
    # HTTPS: https://github.com/owner/repo.git
    https_match = re.match(r"https://github\.com/([^/]+/[^.]+?)(?:\.git)?$", remote_url)
    if https_match:
        return f"https://github.com/{https_match.group(1)}/compare/{target}...{source}?expand=1"
    return ""




# =============================================================================
# SIMPLE COMPARISON & MERGE LOGIC
# =============================================================================


# =============================================================================
# MERGE CACHE  (self-contained — no import from merge.py to avoid circular deps)
# Writes to the same .gitship/merge-cache directory that merge.py uses,
# so both modules interoperate on the same on-disk state.
# =============================================================================

def _merge_cache_dir(repo_path: Path) -> Path:
    """Return (and create) .gitship/merge-cache."""
    d = repo_path / ".gitship" / "merge-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_merge_cache(repo_path: Path, source: str, target: str) -> list:
    """
    Copy all currently-staged files to the merge cache so a future session
    can restore partial conflict resolutions.  Returns list of saved paths.
    """
    import shutil
    cache_dir = _merge_cache_dir(repo_path)

    (cache_dir / "merge-meta.txt").write_text(
        f"source={source}\ntarget={target}\n", encoding="utf-8"
    )

    staged = run_git(["diff", "--cached", "--name-only"], repo_path)
    files = [f.strip() for f in staged.stdout.strip().splitlines() if f.strip()]

    saved = []
    for filepath in files:
        src = repo_path / filepath
        if src.exists():
            dst = cache_dir / filepath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            saved.append(filepath)

    if saved:
        (cache_dir / "resolved-files.txt").write_text(
            "\n".join(saved), encoding="utf-8"
        )
        print(f"{Colors.DIM}💾 Saved {len(saved)} resolved file(s) to merge cache{Colors.RESET}")

    return saved


def _clear_merge_cache(repo_path: Path):
    """Remove the merge cache directory after a successful merge."""
    import shutil
    cache_dir = repo_path / ".gitship" / "merge-cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


def _has_merge_cache(repo_path: Path) -> bool:
    """Return True if a non-empty merge cache exists."""
    meta = repo_path / ".gitship" / "merge-cache" / "merge-meta.txt"
    return meta.exists()


def _fetch_remote_quietly(repo_path: Path, remote: str = "origin") -> bool:
    """Fetch from remote silently. Returns True on success, False if offline/no remote."""
    res = run_git(["fetch", remote, "--prune"], repo_path)
    return res.returncode == 0


def _remote_branch_exists(repo_path: Path, branch: str, remote: str = "origin") -> bool:
    """Return True if origin/<branch> exists locally (after fetch)."""
    res = run_git(["rev-parse", "--verify", f"refs/remotes/{remote}/{branch}"], repo_path)
    return res.returncode == 0


def _branch_divergence(repo_path: Path, branch: str, remote: str = "origin"):
    """
    Return (ahead, behind) counts of local branch vs origin/branch.
    Returns (0, 0) if no remote tracking exists.
    """
    if not _remote_branch_exists(repo_path, branch, remote):
        return (0, 0)
    res = run_git(
        ["rev-list", "--left-right", "--count", f"{remote}/{branch}...{branch}"],
        repo_path
    )
    if res.returncode != 0:
        return (0, 0)
    parts = res.stdout.strip().split()
    if len(parts) == 2:
        return (int(parts[1]), int(parts[0]))  # (local_ahead, local_behind)
    return (0, 0)


def _sync_branch_with_remote(repo_path: Path, branch: str, remote: str = "origin") -> bool:
    """
    Rebase local branch on top of its remote counterpart.
    If conflicts arise, invokes gitship's resolver when available.
    Returns True if the branch is clean and synced, False on failure.
    """
    if not _remote_branch_exists(repo_path, branch, remote):
        return True  # nothing to sync against

    ahead, behind = _branch_divergence(repo_path, branch, remote)
    if behind == 0:
        return True  # already up to date

    print(f"   Rebasing '{branch}' onto {remote}/{branch} ({behind} new commit(s) from remote)...")
    res = run_git(["rebase", f"{remote}/{branch}"], repo_path)
    if res.returncode == 0:
        print(f"{Colors.GREEN}   ✓ Synced '{branch}' with {remote}/{branch}{Colors.RESET}")
        return True

    # Rebase hit conflicts
    conflict_files = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip().splitlines()
    print(f"\n{Colors.RED}   Rebase conflict in {len(conflict_files)} file(s):{Colors.RESET}")
    for f in conflict_files:
        print(f"     {Colors.RED}✗{Colors.RESET} {f}")

    resolved = False
    try:
        from gitship.resolve import run_conflict_resolver
        print(f"\n{Colors.CYAN}   Launching conflict resolver...{Colors.RESET}")
        run_conflict_resolver(repo_path)
        remaining = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip()
        if not remaining:
            res2 = run_git(["rebase", "--continue"], repo_path)
            if res2.returncode == 0:
                print(f"{Colors.GREEN}   ✓ Rebase completed after resolution.{Colors.RESET}")
                resolved = True
    except ImportError:
        pass

    if not resolved:
        print(f"\n  Options:")
        print(f"  1. Keep remote version for all conflicts and continue")
        print(f"  2. Abort rebase (branch left unchanged)")
        choice = safe_input(f"\n  Choice (1-2): ").strip()
        if choice == '1':
            for f in conflict_files:
                run_git(["checkout", "--theirs", f], repo_path)
                run_git(["add", f], repo_path)
            res2 = run_git(["rebase", "--continue"], repo_path)
            if res2.returncode == 0:
                print(f"{Colors.GREEN}   ✓ Rebase completed (remote versions kept){Colors.RESET}")
                return True
        run_git(["rebase", "--abort"], repo_path)
        print(f"{Colors.YELLOW}   Rebase aborted. Branch unchanged.{Colors.RESET}")
        return False

    return resolved


def smart_push_branch(repo_path: Path, branch: str, remote: str = "origin") -> bool:
    """
    Push branch to remote, handling all common rejection scenarios:
      - Remote ahead (fetch first) → rebase then push
      - Diverged → offer rebase or force-with-lease
      - Protected branch → show PR link
      - No upstream → push -u
    Returns True on success.
    """
    # Ensure we have fresh remote state
    _fetch_remote_quietly(repo_path, remote)
    ahead, behind = _branch_divergence(repo_path, branch, remote)

    force_with_lease = False

    if behind > 0 and ahead > 0:
        # Diverged — need user decision
        print(f"\n{Colors.YELLOW}⚠  '{branch}' has diverged from {remote}/{branch}:{Colors.RESET}")
        print(f"   Local is {ahead} ahead and {behind} behind the remote.")
        print(f"\n   1. Rebase local on remote, then push  (recommended — keeps linear history)")
        print(f"   2. Force push with lease  (overwrites remote — only if you're certain)")
        print(f"   3. Cancel")
        choice = safe_input(f"\nChoice (1-3): ").strip()
        if choice == '1':
            if not _sync_branch_with_remote(repo_path, branch, remote):
                return False
        elif choice == '2':
            force_with_lease = True
        else:
            print(f"{Colors.YELLOW}Push cancelled.{Colors.RESET}")
            return False
    elif behind > 0:
        # Remote simply has new commits — safe auto-rebase
        if not _sync_branch_with_remote(repo_path, branch, remote):
            return False

    # Build push args
    if force_with_lease:
        push_args = ["push", "--force-with-lease", remote, branch]
    elif not _remote_branch_exists(repo_path, branch, remote):
        push_args = ["push", "-u", remote, branch]
    else:
        push_args = ["push", remote, branch]

    res = run_git(push_args, repo_path)

    if res.returncode == 0:
        print(f"{Colors.GREEN}✓ Pushed '{branch}' → {remote}/{branch}{Colors.RESET}")
        # Surface any security notices from GitHub without the noise
        for line in res.stderr.splitlines():
            if "vulnerabilit" in line.lower() or "security" in line.lower():
                print(f"  {Colors.YELLOW}⚠  {line.strip()}{Colors.RESET}")
        return True

    # Push failed — explain clearly
    err = res.stderr.strip()
    print(f"\n{Colors.RED}✗ Push failed.{Colors.RESET}")
    if "protected" in err or "pull request" in err.lower() or "Cannot update this protected" in err:
        print(f"  {Colors.YELLOW}🔒 Branch '{branch}' is protected on {remote}.{Colors.RESET}")
        print(f"     Direct pushes are blocked — you need to open a Pull Request.")
        remote_url = run_git(["remote", "get-url", remote], repo_path).stdout.strip()
        default = get_default_branch(repo_path) or "main"
        pr_url = _github_pr_url(remote_url, branch, default)
        if pr_url:
            print(f"\n  {Colors.BRIGHT_BLUE}Open PR: {pr_url}{Colors.RESET}")
    elif "fetch first" in err or "non-fast-forward" in err:
        print(f"  Remote still has commits that aren't local. Try the merge again.")
    else:
        print(f"  {err}")
    return False


def merge_branches_interactive(repo_path: Path, source: str, target: str):
    """
    Merge source into target interactively.

    Smart remote handling:
    - Fetches remote state before doing anything
    - Syncs target with its remote BEFORE merging (prevents push rejection)
    - Uses smart_push_branch which auto-rebases if remote moved between merge and push
    - Conflict handling during merge: save cache, launch resolver, commit on resolution
    - On success: clear merge cache, restore stash, offer branch cleanup, smart push
    - Stash is ALWAYS restored/cleaned — no leaks
    """
    print(f"\n{Colors.BOLD}🔀 MERGE: {Colors.CYAN}{source}{Colors.RESET} → {Colors.CYAN}{target}{Colors.RESET}")

    # ── Unrelated histories check ─────────────────────────────────────────────
    if not has_common_ancestor(repo_path, source, target):
        print(f"\n{Colors.YELLOW}⚠️  Cannot proceed: '{source}' and '{target}' have no common ancestor.{Colors.RESET}")
        print(f"   Switching to unrelated-histories handler...\n")
        handle_unrelated_histories(repo_path, source=source, target=target)
        return

    # ── Fetch so divergence info is accurate ─────────────────────────────────
    print(f"{Colors.DIM}Fetching latest remote state...{Colors.RESET}")
    _fetch_remote_quietly(repo_path)

    tgt_ahead, tgt_behind = _branch_divergence(repo_path, target)

    # ── Show what we're about to do ───────────────────────────────────────────
    print(f"\n  Source : {Colors.CYAN}{source}{Colors.RESET}")
    print(f"  Target : {Colors.CYAN}{target}{Colors.RESET}", end="")
    if tgt_behind:
        print(f"  {Colors.YELLOW}(⚠ {tgt_behind} commit(s) behind remote — will sync first){Colors.RESET}", end="")
    print()

    print(f"\nSteps:")
    if tgt_behind:
        print(f"  1. Sync '{target}' with its remote")
        print(f"  2. Merge '{source}' into '{target}'")
        print(f"  3. Push updated '{target}' (optional)")
    else:
        print(f"  1. Merge '{source}' into '{target}'")
        print(f"  2. Push updated '{target}' (optional)")

    confirm = safe_input(f"\n{Colors.YELLOW}Continue? (y/n):{Colors.RESET} ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        return

    # ── Stash background noise ────────────────────────────────────────────────
    # Show exactly what will be stashed so nothing is ever silently lost
    _pre_stash_status = run_git(["status", "--porcelain"], repo_path)
    _dirty = [l[3:].strip() for l in _pre_stash_status.stdout.strip().splitlines() if l.strip()] if _pre_stash_status.returncode == 0 else []
    stashed = stash_ignored_changes(repo_path, f"Before merge {source} into {target}")
    if stashed and _dirty:
        print(f"{Colors.DIM}   Stashed {len(_dirty)} file(s): {', '.join(_dirty)}{Colors.RESET}")
        print(f"{Colors.DIM}   Restore with: git stash apply stash@{{0}}{Colors.RESET}")

    # ── Switch to target ──────────────────────────────────────────────────────
    print(f"\n{Colors.DIM}Switching to '{target}'...{Colors.RESET}")
    res_checkout = run_git(["checkout", target], repo_path)
    if res_checkout.returncode != 0:
        print(f"{Colors.RED}❌ Failed to switch to '{target}': {res_checkout.stderr.strip()}{Colors.RESET}")
        if stashed:
            restore_latest_stash(repo_path)
        return

    # ── Sync target with remote BEFORE merging ────────────────────────────────
    if tgt_behind > 0:
        print(f"{Colors.DIM}Syncing '{target}' with remote ({tgt_behind} behind)...{Colors.RESET}")
        if not _sync_branch_with_remote(repo_path, target):
            print(f"{Colors.RED}❌ Could not sync '{target}' with remote. Aborting to prevent conflicts.{Colors.RESET}")
            run_git(["checkout", "-"], repo_path)
            if stashed:
                restore_latest_stash(repo_path)
            return

    # ── Capture pre-merge HEAD so generate_merge_message has the right base ─────
    pre_merge_head = run_git(["rev-parse", "HEAD"], repo_path).stdout.strip()

    # ── Merge source into target (no-commit so we can review the message) ───────
    print(f"{Colors.DIM}Merging '{source}' into '{target}'...{Colors.RESET}")

    res_merge = run_git(["merge", "--no-ff", "--no-commit", source], repo_path, check=False)

    # --no-commit leaves us in one of four states:
    #   a) clean staged merge ready to commit            (returncode 0)
    #   b) "Already up to date." — nothing to merge      (returncode 1, stdout contains message)
    #   c) conflicts need resolving                      (returncode 1, conflict markers present)
    #   d) unexpected hard failure                       (returncode != 0, nothing staged)

    merge_out = res_merge.stdout.strip()
    merge_err = res_merge.stderr.strip()

    # State b: already merged — nothing to do
    if "Already up to date" in merge_out or "Already up to date" in merge_err:
        print(f"\n{Colors.YELLOW}ℹ  Already up to date — nothing to merge.{Colors.RESET}")
        if stashed:
            restore_latest_stash(repo_path)
        return

    has_conflicts = bool(
        run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip()
    )
    has_staged = bool(
        run_git(["diff", "--cached", "--name-only"], repo_path).stdout.strip()
    )

    if res_merge.returncode != 0 and has_conflicts:
        # ── State c: Conflict ─────────────────────────────────────────────────
        conflict_files = run_git(
            ["diff", "--name-only", "--diff-filter=U"], repo_path
        ).stdout.strip().splitlines()
        print(f"\n{Colors.RED}❌ Merge has {len(conflict_files)} conflicted file(s):{Colors.RESET}")
        for f in conflict_files:
            print(f"   {Colors.RED}✗{Colors.RESET} {f}")

        # Save cache immediately so nothing is lost if the process exits
        _save_merge_cache(repo_path, source, target)

        # Launch the resolver FIRST — stash stays until after conflicts are fixed
        # (git refuses to pop a stash onto a tree with conflict markers)
        resolved = False
        try:
            from gitship.resolve import run_conflict_resolver
            print(f"\n{Colors.CYAN}Launching interactive conflict resolver...{Colors.RESET}")
            run_conflict_resolver(repo_path)
        except ImportError:
            # Fall back to subprocess so the user still gets the resolver
            import subprocess as _sp
            _sp.run(["gitship", "resolve"], cwd=repo_path)

        remaining = run_git(
            ["diff", "--name-only", "--diff-filter=U"], repo_path
        ).stdout.strip()
        if remaining:
            _save_merge_cache(repo_path, source, target)
            print(f"{Colors.YELLOW}⚠  {len(remaining.splitlines())} conflict(s) still unresolved.{Colors.RESET}")
        else:
            resolved = True

        # Now restore the stash — conflicts are gone so pop will succeed
        if stashed:
            restore_latest_stash(repo_path)
            stashed = False

        if not resolved:
            print(f"\n{Colors.YELLOW}Run 'gitship resolve' when ready to finish resolving.{Colors.RESET}")
            print(f"{Colors.DIM}   Progress is saved — partial resolutions won't be lost.{Colors.RESET}")
            return

    elif res_merge.returncode != 0 and not has_conflicts and not has_staged:
        # ── State d: Hard failure — show everything git said ──────────────────
        print(f"\n{Colors.RED}❌ Merge failed.{Colors.RESET}")
        if merge_out:
            print(f"   stdout: {merge_out}")
        if merge_err:
            print(f"   stderr: {merge_err}")
        if stashed:
            restore_latest_stash(repo_path)
        return

    # ── States a/c-resolved: merge is staged, ready to commit ─────────────────
    print(f"\n{Colors.GREEN}✅ Merge staged successfully.{Colors.RESET}")

    commit_msg = None
    try:
        from gitship.merge_message import generate_merge_message
        # Use pre_merge_head as base so the message covers exactly the incoming commits
        commit_msg = generate_merge_message(
            repo_path=repo_path, base_ref=pre_merge_head, head_ref=source
        )

        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}PROPOSED MERGE COMMIT MESSAGE:{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(commit_msg)
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")

        msg_choice = safe_input(
            f"\n{Colors.CYAN}Use this message? (y / e to edit / n for default):{Colors.RESET} "
        ).strip().lower()

        if msg_choice == 'e':
            import tempfile, os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tf:
                tf.write(commit_msg)
                tmp_path = tf.name
            editor = (os.environ.get('GIT_EDITOR') or os.environ.get('VISUAL')
                      or os.environ.get('EDITOR', 'nano'))
            __import__('subprocess').run([editor, tmp_path])
            commit_msg = open(tmp_path, encoding='utf-8').read().strip()
            os.unlink(tmp_path)
            print(f"{Colors.GREEN}✓ Using edited message.{Colors.RESET}")
        elif msg_choice != 'y':
            commit_msg = None  # use git's default MERGE_MSG
    except ImportError:
        pass  # no merge_message module — use git default

    # ── Commit ────────────────────────────────────────────────────────────────
    if commit_msg:
        # Write message to a temp file and use -F to avoid ARG_MAX limit
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as _tf:
            _tf.write(commit_msg)
            _tf_path = _tf.name
        try:
            res_commit = run_git(["commit", "-F", _tf_path], repo_path)
        finally:
            _os.unlink(_tf_path)
    else:
        res_commit = run_git(["commit", "--no-edit"], repo_path)

    if res_commit.returncode != 0:
        # Show everything — stdout often has the real reason (e.g. "nothing to commit")
        out = res_commit.stdout.strip()
        err = res_commit.stderr.strip()
        print(f"\n{Colors.RED}❌ Commit failed.{Colors.RESET}")
        if out:
            print(f"   {out}")
        if err:
            print(f"   {err}")
        print(f"   Run 'gitship merge' to retry.")
        if stashed:
            restore_latest_stash(repo_path)
        return

    print(f"{Colors.GREEN}✅ Merge committed.{Colors.RESET}")
    _clear_merge_cache(repo_path)

    # ── Restore stash (if not already restored above) ─────────────────────────
    if stashed:
        restore_latest_stash(repo_path)

    # ── Cleanup: offer to delete now-redundant source branch ─────────────────
    verify_and_offer_delete(repo_path, source, target)

    # ── Push (smart — handles remote having moved) ────────────────────────────
    do_push = safe_input(f"\n{Colors.CYAN}🚀 Push updated '{target}' to remote? (y/n):{Colors.RESET} ").strip().lower()
    if do_push == 'y':
        smart_push_branch(repo_path, target)

    # ── Switch back to source branch (don't strand user on target) ───────────
    current = get_current_branch(repo_path)
    if current == target:
        go_back = safe_input(f"\n{Colors.CYAN}Switch back to '{source}'? (y/n):{Colors.RESET} ").strip().lower()
        if go_back == 'y':
            run_git(["checkout", source], repo_path)
            print(f"{Colors.GREEN}✓ Back on '{source}'{Colors.RESET}")


def export_comparison(repo_path: Path, source: str, target: str, commits_1: List[str], commits_2: List[str]):
    """Export full comparison report: commit subjects+bodies, file stats, full diff."""
    try:
        from gitship.config import load_config
        config = load_config()
        export_dir = Path(config.get('export_path', Path.home() / 'gitship_exports'))
    except ImportError:
        export_dir = Path.home() / 'gitship_exports'

    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f"compare_{source}_vs_{target}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = export_dir / filename
    W = 72

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("=" * W + "\n")
        f.write(f"BRANCH COMPARISON: {source} vs {target}\n")
        f.write(f"Repository: {repo_path}\n")
        f.write(f"Generated:  {datetime.now()}\n")
        f.write("=" * W + "\n\n")

        # ── Incoming commits — full subject + body ────────────────────────────
        f.write(f"COMMITS ONLY IN {source} ({len(commits_1)} total):\n")
        f.write("-" * W + "\n")
        if commits_1:
            log_res = run_git(
                ["log", "--format=commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n    %s%n%b%n",
                 "--date=short", f"{target}..{source}"],
                repo_path
            )
            f.write(log_res.stdout if log_res.stdout.strip() else "(no log output)\n")
        else:
            f.write("(None — already merged or source is behind target)\n")
        f.write("\n")

        # ── Outgoing commits (target ahead of source) ─────────────────────────
        f.write(f"COMMITS ONLY IN {target} ({len(commits_2)} total):\n")
        f.write("-" * W + "\n")
        if commits_2:
            log_res2 = run_git(
                ["log", "--format=commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n    %s%n%b%n",
                 "--date=short", f"{source}..{target}"],
                repo_path
            )
            f.write(log_res2.stdout if log_res2.stdout.strip() else "(no log output)\n")
        else:
            f.write("(None)\n")
        f.write("\n")

        # ── File stat summary ─────────────────────────────────────────────────
        f.write("FILE CHANGES (stat):\n")
        f.write("-" * W + "\n")
        stat_res = run_git(["diff", "--stat", f"{target}...{source}"], repo_path)
        f.write(stat_res.stdout.strip() if stat_res.stdout.strip() else "(no changes)")
        f.write("\n\n")

        # ── Full unified diff ─────────────────────────────────────────────────
        f.write("FULL DIFF:\n")
        f.write("-" * W + "\n")
        diff_res = run_git(["diff", "--no-color", f"{target}...{source}"], repo_path)
        f.write(diff_res.stdout if diff_res.stdout.strip() else "(no content changes)\n")

    print(f"{Colors.GREEN}✅ Exported to: {filepath}{Colors.RESET}")


def _offer_cherry_pick_commit_amend(repo_path: Path, source: str, target: str):
    """
    After a successful cherry-pick, offer to amend the HEAD commit message
    with a detailed summary generated by merge_message + user input.
    
    Cherry-pick auto-commits using the original commit's message, which is
    usually a single terse line. This gives the user a chance to replace it
    with a rich message showing file stats, categorized changes, and their
    own context — the same quality as a full merge commit message.
    """
    print(f"\n{Colors.BOLD}📝 Commit Message{Colors.RESET}")
    print(f"The cherry-pick was committed with the original message.")
    print(f"  1. Amend with detailed stats + your notes  {Colors.DIM}(recommended){Colors.RESET}")
    print(f"  2. Keep original message as-is")
    
    try:
        choice = safe_input(f"\n{Colors.CYAN}Choice (1-2):{Colors.RESET} ").strip()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        print("\nKeeping original message.")
        return
    
    if choice != "1":
        return
    
    # Generate the stats-based body via merge_message
    print(f"{Colors.DIM}Generating change summary...{Colors.RESET}")
    generated_body = ""
    try:
        from gitship.merge_message import generate_merge_message
        # Use the pre-cherry-pick state of target as base (HEAD~N where N = commits picked)
        # Simpler and always correct: diff HEAD against the merge-base with source
        merge_base_res = run_git(["merge-base", source, target], repo_path)
        base_ref = merge_base_res.stdout.strip() if merge_base_res.returncode == 0 else f"{target}~1"
        generated_body = generate_merge_message(
            repo_path=repo_path,
            base_ref=base_ref,
            head_ref=target  # target now contains the picked commits
        )
    except ImportError:
        print(f"{Colors.YELLOW}  merge_message not available — stats section will be skipped.{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.YELLOW}  Could not generate stats: {e}{Colors.RESET}")
    
    # Get current HEAD message so user can keep the subject line if they want
    current_msg_res = run_git(["log", "-1", "--pretty=%B"], repo_path)
    current_subject = current_msg_res.stdout.strip().split('\n')[0] if current_msg_res.returncode == 0 else ""
    
    # Let the user write their own subject / notes
    print(f"\n{Colors.BOLD}Step 1: Subject line{Colors.RESET}")
    print(f"{Colors.DIM}Current: {current_subject}{Colors.RESET}")
    print("Enter a new subject, or press Enter to keep the current one:")
    try:
        new_subject = safe_input("Subject: ").strip()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        print("\nAmend cancelled.")
        return
    subject = new_subject if new_subject else current_subject
    
    print(f"\n{Colors.BOLD}Step 2: Your notes (optional){Colors.RESET}")
    print("Add context about what was cherry-picked and why.")
    print("  1. Type inline  (end with blank line)")
    print("  2. Open editor")
    print("  3. Skip")
    try:
        notes_choice = safe_input("Choose (1-3): ").strip()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        notes_choice = "3"
    
    user_notes = ""
    if notes_choice == "1":
        print(f"{Colors.DIM}Type your notes. When done, type 'END' on its own line:{Colors.RESET}")
        note_lines = []
        try:
            while True:
                line = safe_input()
                if line.strip().upper() == "END":
                    break
                note_lines.append(line)
        except (KeyboardInterrupt, EOFError, UserCancelled):
            pass
        user_notes = "\n".join(note_lines).strip()
        
        # Drain any remaining buffered stdin from paste so it doesn't
        # bleed into subsequent input() calls (confirm prompt etc.)
        import sys, termios, tty
        try:
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass  # Non-TTY or Windows — best-effort only
    
    elif notes_choice == "2":
        import tempfile, os
        template = (
            f"# Cherry-pick: {source} → {target}\n"
            "# Lines starting with # are ignored.\n"
            "# Describe what was cherry-picked and why.\n\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tf:
            temp_path = tf.name
            tf.write(template)
        editor = os.environ.get('EDITOR', 'nano')
        try:
            import subprocess
            subprocess.run([editor, temp_path], check=True)
            with open(temp_path, 'r', encoding='utf-8') as f:
                raw = f.read()
            note_lines = [l.rstrip() for l in raw.splitlines() if not l.strip().startswith('#')]
            while note_lines and not note_lines[0]: note_lines.pop(0)
            while note_lines and not note_lines[-1]: note_lines.pop()
            user_notes = "\n".join(note_lines).strip()
        except Exception as e:
            print(f"{Colors.YELLOW}  Editor error: {e}{Colors.RESET}")
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
    
    # Assemble the final message
    parts = [subject, ""]
    if user_notes:
        parts.append(user_notes)
        parts.append("")
    if generated_body:
        # Strip the generated title line — we already have our own subject
        gen_lines = generated_body.split('\n')
        body_only = '\n'.join(gen_lines[1:]).lstrip('\n')
        if body_only.strip():
            parts.append(body_only)
    parts.append("[gitship-generated]")
    
    final_message = '\n'.join(parts).strip()
    
    # Preview — show full message, paged if long, before asking to confirm
    print(f"\n{Colors.BOLD}Final commit message:{Colors.RESET}")
    preview_lines = final_message.split('\n')
    page_size = 30
    
    if len(preview_lines) <= page_size:
        for line in preview_lines:
            print(f"  {Colors.CYAN}{line}{Colors.RESET}")
    else:
        # Show in pages so user can actually read it
        for i in range(0, len(preview_lines), page_size):
            chunk = preview_lines[i:i + page_size]
            for line in chunk:
                print(f"  {Colors.CYAN}{line}{Colors.RESET}")
            remaining = len(preview_lines) - (i + page_size)
            if remaining > 0:
                try:
                    cont = safe_input(f"\n  {Colors.DIM}--- {remaining} more lines, Enter to continue, 'q' to stop paging ---{Colors.RESET} ").strip().lower()
                    if cont == 'q':
                        print(f"  {Colors.DIM}(message continues...){Colors.RESET}")
                        break
                except (KeyboardInterrupt, EOFError, UserCancelled):
                    break
    
    try:
        confirm = safe_input(f"\n{Colors.YELLOW}Amend commit with this message? (y/n):{Colors.RESET} ").strip().lower()
    except (KeyboardInterrupt, EOFError, UserCancelled):
        print("\nAmend cancelled.")
        return
    
    if confirm == 'y':
        amend_res = run_git(["commit", "--amend", "-m", final_message], repo_path)
        if amend_res.returncode == 0:
            print(f"{Colors.GREEN}✓ Commit message updated.{Colors.RESET}")
            
            # Offer to push — with pull --rebase first to handle diverged remote
            try:
                push_choice = safe_input(f"\n{Colors.CYAN}Push '{target}' to remote? (y/n):{Colors.RESET} ").strip().lower()
            except (KeyboardInterrupt, EOFError, UserCancelled):
                push_choice = 'n'
            
            if push_choice == 'y':
                print(f"{Colors.DIM}Pulling remote changes (rebase) first...{Colors.RESET}")
                pull_res = run_git(["pull", "--rebase", "origin", target], repo_path)
                if pull_res.returncode != 0:
                    print(f"{Colors.YELLOW}⚠️  Pull --rebase failed:{Colors.RESET}")
                    print(pull_res.stderr.strip())
                    print(f"{Colors.YELLOW}Resolve conflicts, then push manually: git push origin {target}{Colors.RESET}")
                else:
                    # Parse and surface any skipped commits — these are commits git
                    # silently drops during rebase because it detects them as already
                    # present (e.g. from a previous cherry-pick session).
                    pull_output = pull_res.stdout + pull_res.stderr
                    skipped = []
                    for line in pull_output.splitlines():
                        if "skipped previously applied commit" in line:
                            sha = line.strip().split()[-1]
                            # Get the subject for that sha
                            subj_res = run_git(["log", "-1", "--pretty=%s", sha], repo_path)
                            subj = subj_res.stdout.strip() if subj_res.returncode == 0 else ""
                            skipped.append(f"{sha[:8]} {subj}" if subj else sha[:8])
                    
                    if skipped:
                        print(f"{Colors.YELLOW}ℹ️  Rebase skipped {len(skipped)} commit(s) already present in remote:{Colors.RESET}")
                        for s in skipped:
                            print(f"   {Colors.DIM}• {s}{Colors.RESET}")
                        print(f"{Colors.DIM}   These were detected as duplicates of prior cherry-picks — this is expected.{Colors.RESET}")
                    elif pull_res.stdout.strip():
                        print(pull_res.stdout.strip())
                    
                    push_res = run_git(["push", "origin", target], repo_path)
                    if push_res.returncode == 0:
                        print(f"{Colors.GREEN}✓ Pushed to origin/{target}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}✗ Push failed: {push_res.stderr.strip()}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Amend failed: {amend_res.stderr.strip()}{Colors.RESET}")
    else:
        print("Keeping original message.")


def compare_branches_simple(repo_path: Path, source: str, target: str):
    """Show directional comparison: Source -> Target."""
    
    # Check for broken state before doing anything
    if not ensure_clean_git_state(repo_path):
        print(f"\n{Colors.RED}Cannot proceed with review while git state is interrupted.{Colors.RESET}")
        return

    # Fetch so remote-divergence info is current (silent — offline safe)
    _fetch_remote_quietly(repo_path)

    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}REVIEW: {Colors.CYAN}{source}{Colors.RESET} (Source) ➜ {Colors.CYAN}{target}{Colors.RESET} (Target)")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
    
    # 1. Incoming Commits (What Source adds to Target)
    res_incoming = run_git(["log", "--oneline", f"{target}..{source}"], repo_path)
    incoming_list = [line for line in res_incoming.stdout.strip().split('\n') if line]
    
    print(f"📦 {Colors.BOLD}INCOMING CHANGES{Colors.RESET} (Commits in {source} missing from {target}):")
    if incoming_list:
        print(f"   {Colors.GREEN}{len(incoming_list)} commits{Colors.RESET} to merge/apply:")
        for line in incoming_list[:10]:
            print(f"   {Colors.GREEN}+{Colors.RESET} {line}")
        if len(incoming_list) > 10:
            print(f"     ... and {len(incoming_list)-10} more")
            
        print(f"\n📄 {Colors.BOLD}FILE CHANGES{Colors.RESET} (The Patch):")
        # 3-dot diff: Changes in source since it diverged from target
        stats = run_git(["diff", "--stat", f"{target}...{source}"], repo_path)
        if stats.stdout.strip():
            print(stats.stdout.rstrip())
        else:
            print("   (no file changes detected)")
    else:
        print(f"   {Colors.YELLOW}(None - {source} is already merged or behind {target}){Colors.RESET}")

    print("-" * 60)

    # 2. Target Ahead Status (Context only)
    res_missing = run_git(["log", "--oneline", f"{source}..{target}"], repo_path)
    missing_list = [line for line in res_missing.stdout.strip().split('\n') if line]
    
    if missing_list:
        print(f"🔒 {Colors.BOLD}TARGET STATUS{Colors.RESET}: {target} is {len(missing_list)} commits ahead of source base.")
    else:
        print(f"🔒 {Colors.BOLD}TARGET STATUS{Colors.RESET}: {target} is up to date with source base.")

    # --- Merge Analysis ---
    print(f"\n{Colors.BOLD}ANALYSIS:{Colors.RESET}")
    
    if not incoming_list:
        print(f"✅ {Colors.GREEN}Already merged{Colors.RESET} or nothing to apply.")
    else:
        # Detect unrelated histories BEFORE trying merge-base (which will fail)
        if not has_common_ancestor(repo_path, source, target):
            print(f"{Colors.YELLOW}⚠️  UNRELATED HISTORIES — no common ancestor found.{Colors.RESET}")
            print(f"   Standard merge is not possible. See options below.\n")
            print(f"\n{Colors.BOLD}ACTIONS:{Colors.RESET}")
            print(f"  1. Resolve unrelated histories ({Colors.CYAN}rebase / force-merge / PR{Colors.RESET})")
            print(f"  2. View full diff (content changes)")
            print(f"  3. Swap Source/Target")
            print(f"  0. Back")
            choice = safe_input(f"\n{Colors.BLUE}Choice (0-3):{Colors.RESET} ").strip()
            if choice == "1":
                handle_unrelated_histories(repo_path, source=source, target=target)
            elif choice == "2":
                diff_result = run_git(["diff", source, target], repo_path)
                lines = diff_result.stdout.splitlines()
                for line in lines[:80]:
                    print(f"  {line}")
                if len(lines) > 80:
                    print(f"  ... ({len(lines) - 80} more lines)")
            elif choice == "3":
                compare_branches_simple(repo_path, source=target, target=source)
            return

        # Check for conflicts by looking for overlapping file changes since merge base
        mb_res = run_git(["merge-base", source, target], repo_path)
        merge_base = mb_res.stdout.strip()
        
        # Files changed in Source since base
        files_src = run_git(["diff", "--name-only", f"{merge_base}..{source}"], repo_path)
        set_src = set(files_src.stdout.strip().split('\n')) if files_src.stdout.strip() else set()
        
        # Files changed in Target since base
        files_tgt = run_git(["diff", "--name-only", f"{merge_base}..{target}"], repo_path)
        set_tgt = set(files_tgt.stdout.strip().split('\n')) if files_tgt.stdout.strip() else set()
        
        set_src.discard('')
        set_tgt.discard('')
        
        overlap = set_src & set_tgt
        
        if overlap:
            print(f"{Colors.YELLOW}⚠️  POSSIBLE CONFLICTS{Colors.RESET} - Both branches modified these files:")
            for f in sorted(overlap):
                print(f"   - {f}")
        else:
            print(f"✅ {Colors.GREEN}CLEAN MERGE EXPECTED{Colors.RESET} (No overlapping file changes)")

    # --- Options ---
    print(f"\n{Colors.BOLD}ACTIONS:{Colors.RESET}")
    print(f"  1. Merge {Colors.CYAN}{source}{Colors.RESET} ➜ INTO ➜ {Colors.CYAN}{target}{Colors.RESET}")
    print(f"  2. Cherry-pick commits from {Colors.CYAN}{source}{Colors.RESET} ➜ INTO ➜ {Colors.CYAN}{target}{Colors.RESET}")
    print(f"  3. View commits with full descriptions  ({len(incoming_list)} commits, paged)")
    print(f"  4. Browse diff by file  (preview each file, max 50 lines)")
    print(f"  5. Swap Source/Target (Review other direction)")
    print(f"  6. Export full report  (commit bodies + full diff → .txt)")
    print(f"  0. Back")
    
    choice = safe_input(f"\n{Colors.BLUE}Choice (0-6):{Colors.RESET} ").strip()
    
    if choice == "1":
        merge_branches_interactive(repo_path, source=source, target=target)
    elif choice == "2":
        print(f"\n{Colors.BOLD}🍒 CHERRY-PICK PREVIEW{Colors.RESET}")
        
        # Pre-flight: check if a cherry-pick is already in progress
        cherry_pick_head = repo_path / ".git" / "CHERRY_PICK_HEAD"
        if cherry_pick_head.exists():
            print(f"{Colors.YELLOW}⚠️  A cherry-pick is already in progress on this repo.{Colors.RESET}")
            conflicted = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip()
            if conflicted:
                print(f"\n{Colors.RED}Conflicted files still unresolved:{Colors.RESET}")
                for f in conflicted.split('\n'):
                    print(f"  ✗ {f}")
            else:
                print(f"\n{Colors.GREEN}No conflicted files — all resolved.{Colors.RESET}")
            
            print(f"\n{Colors.BOLD}What would you like to do with the in-progress cherry-pick?{Colors.RESET}")
            print(f"  1. Resume — resolve conflicts interactively, then continue")
            print(f"  2. Abort  — cancel it and restore to pre-cherry-pick state")
            print(f"  3. Back")
            
            try:
                stuck_choice = safe_input(f"\n{Colors.BLUE}Choice (1-3):{Colors.RESET} ").strip()
            except (KeyboardInterrupt, EOFError, UserCancelled):
                return
            
            if stuck_choice == "1":
                # Route to resolver
                if conflicted:
                    try:
                        from gitship.resolve_conflicts import main as resolve_main
                        resolve_main()
                    except ImportError:
                        print(f"{Colors.YELLOW}Conflict resolver not available. Fix manually then run: git cherry-pick --continue{Colors.RESET}")
                        return
                # After resolution (or if already clean), continue
                remaining = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip()
                if remaining:
                    print(f"{Colors.YELLOW}Still unresolved files. Finish resolving before continuing.{Colors.RESET}")
                    return
                cont = safe_input(f"\n{Colors.CYAN}Continue cherry-pick now? (y/n):{Colors.RESET} ").strip().lower()
                if cont == 'y':
                    cont_res = run_git(["cherry-pick", "--continue", "--no-edit"], repo_path)
                    if cont_res.returncode == 0:
                        print(f"{Colors.GREEN}✅ Cherry-pick completed.{Colors.RESET}")
                        _offer_cherry_pick_commit_amend(repo_path, source, target)
                    else:
                        print(f"{Colors.RED}✗ Continue failed: {cont_res.stderr.strip()}{Colors.RESET}")
                return
            
            elif stuck_choice == "2":
                abort_res = run_git(["cherry-pick", "--abort"], repo_path)
                if abort_res.returncode == 0:
                    print(f"{Colors.GREEN}✓ Cherry-pick aborted. Repository restored to clean state.{Colors.RESET}")
                else:
                    print(f"{Colors.RED}✗ Abort failed: {abort_res.stderr.strip()}{Colors.RESET}")
                return
            
            else:
                return

        print(f"This will apply {len(incoming_list)} commit(s) from {Colors.CYAN}{source}{Colors.RESET} to {Colors.CYAN}{target}{Colors.RESET}")
        print(f"\n{Colors.BOLD}Commits to apply:{Colors.RESET}")
        for line in incoming_list[:10]:  # Already formatted "hash message" strings
            print(f"  + {line}")
        if len(incoming_list) > 10:
            print(f"  ... and {len(incoming_list) - 10} more")
        
        print(f"\n{Colors.BOLD}Files that will change:{Colors.RESET}")
        diff_stat = run_git(["diff", "--stat", f"{target}...{source}"], repo_path)
        print(diff_stat.stdout)
        
        confirm = safe_input(f"\n{Colors.YELLOW}Proceed with cherry-pick? (y/n):{Colors.RESET} ").strip().lower()
        if confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
            return
        
        print(f"\n{Colors.BOLD}🍒 Cherry-picking {len(incoming_list)} commits from {source} INTO {target}...{Colors.RESET}")
        
        # 0. Atomic Stash
        _pre_stash_status = run_git(["status", "--porcelain"], repo_path)
        _dirty = [l[3:].strip() for l in _pre_stash_status.stdout.strip().splitlines() if l.strip()] if _pre_stash_status.returncode == 0 else []
        stashed = stash_ignored_changes(repo_path, f"Before cherry-pick {source} into {target}")
        if stashed and _dirty:
            print(f"{Colors.DIM}   Stashed {len(_dirty)} file(s): {', '.join(_dirty)}{Colors.RESET}")
            print(f"{Colors.DIM}   Restore with: git stash apply stash@{{0}}{Colors.RESET}")

        # Safety check: are we on target?
        current = get_current_branch(repo_path)
        if current != target:
            print(f"Switching to {target}...")
            res = run_git(["checkout", target], repo_path)
            if res.returncode != 0:
                print(f"{Colors.RED}❌ Could not switch to {target}{Colors.RESET}")
                if stashed:
                    print(f"{Colors.YELLOW}⚠️  Stash kept. Restoring now...{Colors.RESET}")
                    restore_latest_stash(repo_path)
                return
        
        # Get revisions in chronological order (oldest first), excluding merge commits
        all_revs = run_git(["rev-list", "--reverse", "--no-merges", f"{target}..{source}"], repo_path).stdout.strip().split()
        
        # Also get full list to detect skipped merges
        all_revs_with_merges = run_git(["rev-list", "--reverse", f"{target}..{source}"], repo_path).stdout.strip().split()
        merge_count = len(all_revs_with_merges) - len(all_revs)
        
        if merge_count > 0:
            print(f"{Colors.YELLOW}⚠️  Skipping {merge_count} merge commit(s) (not directly cherry-pickable).{Colors.RESET}")
            print(f"{Colors.DIM}   Tip: Use 'Merge' (option 1) if you want merge commits included.{Colors.RESET}")
        
        revs = all_revs
        if not revs:
            print(f"{Colors.YELLOW}No non-merge commits to cherry-pick. Consider using Merge instead (option 1).{Colors.RESET}")
            if stashed:
                restore_latest_stash(repo_path)
            return

        res = run_git(["cherry-pick"] + revs, repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✅ Successfully applied patches from {source}{Colors.RESET}")
            if stashed:
                restore_latest_stash(repo_path)
            
            # Show what changed
            print(f"\n{Colors.BOLD}Changes applied:{Colors.RESET}")
            show_result = run_git(["show", "--stat", "HEAD"], repo_path)
            print(show_result.stdout)
            
            # Offer to amend with a detailed commit message
            _offer_cherry_pick_commit_amend(repo_path, source, target)
            
            # Offer to push
            push_choice = safe_input(f"\n{Colors.CYAN}Push to remote? (y/n):{Colors.RESET} ").strip().lower()
            if push_choice == 'y':
                push_result = atomic_git_operation(
                    repo_path=repo_path,
                    git_command=["push", "origin", target],
                    description=f"push {target} after cherry-pick"
                )
                if push_result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Pushed to origin/{target}{Colors.RESET}")
                else:
                    print(f"{Colors.RED}✗ Push failed: {push_result.stderr.strip()}{Colors.RESET}")
            
            # Verify and cleanup
            verify_and_offer_delete(repo_path, source, target)

        else:
            # Check if this is just an empty/redundant patch (already applied)
            # Git returns error 1 but stderr contains specific hints
            err_msg = res.stderr + res.stdout
            
            # IMPORTANT: "cherry-pick is already in progress" also triggers
            # "git cherry-pick --skip" in its hint text, so we must exclude it
            # explicitly before checking is_empty, otherwise we'd wrongly skip
            # commits that were never actually applied.
            is_already_in_progress = "cherry-pick is already in progress" in err_msg
            
            is_empty = not is_already_in_progress and (
                "The previous cherry-pick is now empty" in err_msg or \
                "allow-empty" in err_msg or \
                "git cherry-pick --skip" in err_msg
            )
            
            if is_empty:
                print(f"{Colors.YELLOW}💡 One or more commits are empty (already applied or conflict-resolved away).{Colors.RESET}")
                print(f"{Colors.DIM}   Skipping through empty commits...{Colors.RESET}")
                
                # Loop: keep skipping empty commits until cherry-pick completes,
                # hits a real conflict, or genuinely errors out.
                # A batch pick of N commits can have multiple empty ones in sequence.
                skip_loop_limit = len(revs) + 2  # Safety ceiling
                final_skip_res = None
                skipped_count = 0
                
                for _ in range(skip_loop_limit):
                    skip_res = run_git(["cherry-pick", "--skip"], repo_path)
                    skipped_count += 1
                    
                    # Success — no more commits to pick
                    if skip_res.returncode == 0 and not (repo_path / ".git" / "CHERRY_PICK_HEAD").exists():
                        final_skip_res = skip_res
                        break
                    
                    skip_err = skip_res.stderr + skip_res.stdout
                    
                    # Still empty — loop again
                    if "The previous cherry-pick is now empty" in skip_err or \
                       "git cherry-pick --skip" in skip_err:
                        continue
                    
                    # Real conflict hit during skip sequence
                    if skip_res.returncode != 0:
                        final_skip_res = skip_res
                        break
                    
                    # returncode 0 but CHERRY_PICK_HEAD still exists — still in progress
                    # (can happen mid-batch), loop to pick the next commit
                    if (repo_path / ".git" / "CHERRY_PICK_HEAD").exists():
                        # Next commit landed cleanly, check if we're done
                        continue
                    
                    final_skip_res = skip_res
                    break
                
                # Check final state
                pick_still_active = (repo_path / ".git" / "CHERRY_PICK_HEAD").exists()
                conflicted_files = run_git(["diff", "--name-only", "--diff-filter=U"], repo_path).stdout.strip()
                
                if not pick_still_active and not conflicted_files:
                    print(f"{Colors.GREEN}✅ Successfully completed (skipped {skipped_count} empty commit(s)).{Colors.RESET}")
                    if stashed:
                        restore_latest_stash(repo_path)
                    
                    _offer_cherry_pick_commit_amend(repo_path, source, target)
                    
                    # Verify actual file parity before claiming branch is redundant
                    mb_res = run_git(["merge-base", source, target], repo_path)
                    actually_redundant = False
                    if mb_res.returncode == 0:
                        merge_base = mb_res.stdout.strip()
                        files_res = run_git(["diff", "--name-only", f"{merge_base}..{source}"], repo_path)
                        changed_files = [f for f in files_res.stdout.strip().split('\n') if f]
                        mismatches = sum(
                            1 for f in changed_files
                            if run_git(["rev-parse", f"{source}:{f}"], repo_path).stdout.strip() !=
                               run_git(["rev-parse", f"{target}:{f}"], repo_path).stdout.strip()
                        )
                        actually_redundant = (mismatches == 0)
                    
                    if actually_redundant:
                        current_br = get_current_branch(repo_path)
                        confirm_and_delete_branch(
                            repo_path, source, current_br,
                            context=f"All changes from '{source}' are confirmed present in '{target}'."
                        )
                    else:
                        print(f"\n{Colors.YELLOW}⚠️  Some files in '{source}' still differ from '{target}' — branch not deleted.{Colors.RESET}")
                        print(f"   Review the diff before deciding to delete '{source}'.")
                    return
                
                elif conflicted_files:
                    # Skip sequence hit a real conflict — fall through to the conflict handler below
                    print(f"{Colors.YELLOW}Skipped {skipped_count} empty commit(s), but hit a real conflict:{Colors.RESET}")
                    for f in conflicted_files.split('\n'):
                        print(f"  ✗ {f}")
                    # Fall through to the conflict options menu below
                
                else:
                    # Skip loop exhausted or unexpected error
                    print(f"{Colors.RED}✗ Could not complete skip sequence after {skipped_count} attempt(s).{Colors.RESET}")
                    if final_skip_res:
                        print(f"  {final_skip_res.stderr.strip()}")
                    # Fall through to conflict options menu

            # If not empty (or skip failed), it's a real conflict
            print(f"{Colors.RED}❌ Cherry-pick encountered conflicts.{Colors.RESET}")
            print(res.stderr)
            if stashed:
                print(f"\n{Colors.MAGENTA}📦 Note: Background changes are stashed and will be auto-restored when you finish.{Colors.RESET}")
            
            # Only offer interactive resolve if cherry-pick state is actually present on disk.
            # If git errored because a pick was ALREADY in progress (e.g. leftover from a
            # previous session), CHERRY_PICK_HEAD will be absent in repo_path and the
            # resolver will fail with "Not in a cherry-pick state". In that case, force
            # the user to abort/exit so they can deal with the real stuck repo first.
            cherry_pick_active = (repo_path / ".git" / "CHERRY_PICK_HEAD").exists()
            
            print(f"\n{Colors.BOLD}What would you like to do?{Colors.RESET}")
            if cherry_pick_active:
                print(f"  1. Resolve conflicts interactively (guided)")
                print(f"  2. Abort cherry-pick and return to previous state")
                print(f"  3. Leave as-is (fix manually, then run 'git cherry-pick --continue')")
                max_choice = "3"
            else:
                # Cherry-pick state not present here — the error was likely "already in
                # progress" on the target branch. Interactive resolve would fail silently.
                print(f"{Colors.YELLOW}  ⚠️  The cherry-pick state is on '{target}', not the current branch.{Colors.RESET}")
                print(f"{Colors.YELLOW}     Switch to '{target}' and run 'gitship branch' → Compare → Cherry-pick{Colors.RESET}")
                print(f"{Colors.YELLOW}     to resume or abort the stuck operation there.{Colors.RESET}")
                print(f"  1. Abort the stuck cherry-pick on '{target}' now")
                print(f"  2. Leave as-is and exit")
                max_choice = "2"
            
            conflict_choice = safe_input(f"\n{Colors.BLUE}Choice (1-{max_choice}):{Colors.RESET} ").strip()
            
            if cherry_pick_active:
                if conflict_choice == "1":
                    try:
                        from gitship.resolve_conflicts import main as resolve_main
                        resolve_main()
                        # After resolution, prompt to continue
                        cont = safe_input(f"\n{Colors.CYAN}Continue cherry-pick? (y/n):{Colors.RESET} ").strip().lower()
                        if cont == 'y':
                            cont_res = run_git(["cherry-pick", "--continue", "--no-edit"], repo_path)
                            if cont_res.returncode == 0:
                                print(f"{Colors.GREEN}✅ Cherry-pick completed successfully.{Colors.RESET}")
                                _offer_cherry_pick_commit_amend(repo_path, source, target)
                            else:
                                print(f"{Colors.RED}✗ Continue failed: {cont_res.stderr.strip()}{Colors.RESET}")
                    except ImportError:
                        print(f"{Colors.YELLOW}Conflict resolver not available. Fix conflicts manually, then run:{Colors.RESET}")
                        print(f"  git add .")
                        print(f"  git cherry-pick --continue")
                
                elif conflict_choice == "2":
                    abort_res = run_git(["cherry-pick", "--abort"], repo_path)
                    if abort_res.returncode == 0:
                        print(f"{Colors.GREEN}✓ Cherry-pick aborted. Repository restored to previous state.{Colors.RESET}")
                        if stashed:
                            restore_latest_stash(repo_path)
                    else:
                        print(f"{Colors.RED}✗ Abort failed: {abort_res.stderr.strip()}{Colors.RESET}")
                
                else:
                    print(f"\n{Colors.YELLOW}Fix conflicts manually, then run:{Colors.RESET}")
                    print(f"  git add .")
                    print(f"  git cherry-pick --continue")
            
            else:
                # Not cherry_pick_active — target branch has the stuck pick
                if conflict_choice == "1":
                    # Abort on the target branch by temporarily switching to it
                    print(f"\n{Colors.DIM}Switching to '{target}' to abort...{Colors.RESET}")
                    sw = run_git(["checkout", target], repo_path)
                    if sw.returncode != 0:
                        print(f"{Colors.RED}✗ Could not switch to '{target}': {sw.stderr.strip()}{Colors.RESET}")
                        print(f"  Run manually: git checkout {target} && git cherry-pick --abort")
                    else:
                        abort_res = run_git(["cherry-pick", "--abort"], repo_path)
                        if abort_res.returncode == 0:
                            print(f"{Colors.GREEN}✓ Stuck cherry-pick on '{target}' aborted.{Colors.RESET}")
                            # Switch back to where we were
                            run_git(["checkout", source], repo_path)
                            print(f"{Colors.GREEN}✓ Switched back to '{source}'.{Colors.RESET}")
                        else:
                            print(f"{Colors.RED}✗ Abort failed: {abort_res.stderr.strip()}{Colors.RESET}")
                else:
                    print(f"\n{Colors.YELLOW}Left as-is. To clean up manually:{Colors.RESET}")
                    print(f"  git checkout {target}")
                    print(f"  git cherry-pick --abort")
    
    elif choice == "3":
        _show_commits_with_bodies(repo_path, source, target)
        compare_branches_simple(repo_path, source, target)

    elif choice == "4":
        _browse_diff_by_file(repo_path, source, target)
        compare_branches_simple(repo_path, source, target)

    elif choice == "5":
        compare_branches_simple(repo_path, source=target, target=source)

    elif choice == "6":
        export_comparison(repo_path, source, target, incoming_list, missing_list)
        safe_input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")
        compare_branches_simple(repo_path, source, target)


def _show_commits_with_bodies(repo_path: Path, source: str, target: str):
    """Show all incoming commits with subject + body, paged 15 at a time."""
    # Use --format with a unique record-start sentinel so we can split reliably.
    # \x1e is ASCII Record Separator — won't appear in commit messages.
    # Each record: SENTINEL hash\nsubject\nauthor\ndate\n\nbody
    SENTINEL = "\x1e"
    log_res = run_git(
        ["log",
         f"--format={SENTINEL}%H%n%s%n%an%n%ad%n%n%b",
         "--date=short",
         f"{target}..{source}"],
        repo_path
    )
    raw = log_res.stdout

    if not raw.strip():
        print(f"{Colors.YELLOW}(No commits){Colors.RESET}")
        safe_input(f"\n{Colors.DIM}Press Enter...{Colors.RESET}")
        return

    entries = []
    for record in raw.split(SENTINEL):
        record = record.strip()
        if not record:
            continue
        lines = record.splitlines()
        if len(lines) < 1:
            continue
        sha    = lines[0].strip()[:8] if lines else "?"
        subj   = lines[1].strip()     if len(lines) > 1 else ""
        author = lines[2].strip()     if len(lines) > 2 else ""
        date   = lines[3].strip()     if len(lines) > 3 else ""
        # body starts after the blank line (index 4), strip leading/trailing blanks
        body_lines = lines[5:] if len(lines) > 5 else []
        # strip trailing empty lines from body
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        body = "\n".join(body_lines)
        if sha:
            entries.append((sha, subj, body, author, date))

    if not entries:
        fallback = run_git(["log", "--oneline", f"{target}..{source}"], repo_path)
        lines = [l for l in fallback.stdout.strip().splitlines() if l]
        print(f"\n{Colors.BOLD}All {len(lines)} commits:{Colors.RESET}")
        for l in lines:
            print(f"  {Colors.GREEN}+{Colors.RESET} {l}")
        safe_input(f"\n{Colors.DIM}Press Enter...{Colors.RESET}")
        return

    PAGE = 15
    total = len(entries)
    page  = 0

    while True:
        start = page * PAGE
        end   = min(start + PAGE, total)
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}COMMITS {start+1}–{end} of {total}  ({source} not in {target}){Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")

        for sha, subj, body, author, date in entries[start:end]:
            print(f"\n  {Colors.GREEN}{sha}{Colors.RESET}  {Colors.BOLD}{subj}{Colors.RESET}")
            if author or date:
                print(f"  {Colors.DIM}{author}  {date}{Colors.RESET}")
            if body:
                blines = body.splitlines()
                for bline in blines[:6]:
                    print(f"  {Colors.DIM}│ {bline}{Colors.RESET}")
                if len(blines) > 6:
                    print(f"  {Colors.DIM}│ ... ({len(blines)-6} more lines){Colors.RESET}")

        nav = []
        if end < total:
            nav.append("n=next")
        if page > 0:
            nav.append("p=prev")
        nav.append("Enter=back")
        resp = safe_input(
            f"\n  Page {page+1}/{(total+PAGE-1)//PAGE}  [{' | '.join(nav)}]: "
        ).strip().lower()
        if resp == 'n' and end < total:
            page += 1
        elif resp == 'p' and page > 0:
            page -= 1
        else:
            break


def _browse_diff_by_file(repo_path: Path, source: str, target: str):
    """
    List all changed files; user picks one to see a 50-line preview.
    Loops until user presses 0/Enter to go back.
    """
    name_res  = run_git(["diff", "--name-only",  f"{target}...{source}"], repo_path)
    stat_res  = run_git(["diff", "--stat",        f"{target}...{source}"], repo_path)
    changed_files = [f for f in name_res.stdout.strip().splitlines() if f]

    if not changed_files:
        print(f"{Colors.YELLOW}(No file changes){Colors.RESET}")
        safe_input(f"\n{Colors.DIM}Press Enter...{Colors.RESET}")
        return

    stat_lines = stat_res.stdout.rstrip().splitlines()

    while True:
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}CHANGED FILES — {len(changed_files)} files{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")

        # Print stat lines with index numbers (skip last summary line)
        for i, sline in enumerate(stat_lines[:-1], 1):
            print(f"  {i:>3}.{sline}")
        if stat_lines:
            print(f"\n  {Colors.DIM}{stat_lines[-1].strip()}{Colors.RESET}")

        print(f"\n  0. Back")
        sel = safe_input(f"\n{Colors.CYAN}File number to preview (0=back): {Colors.RESET}").strip()

        if not sel or sel == '0':
            break
        try:
            idx = int(sel) - 1
        except ValueError:
            print(f"{Colors.RED}Invalid.{Colors.RESET}")
            continue
        if not (0 <= idx < len(changed_files)):
            print(f"{Colors.RED}Invalid.{Colors.RESET}")
            continue

        chosen = changed_files[idx]
        file_diff = run_git(
            ["diff", "--no-color", f"{target}...{source}", "--", chosen], repo_path
        )
        diff_lines = file_diff.stdout.splitlines()

        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}DIFF: {chosen}{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")

        LIMIT = 50
        for line in diff_lines[:LIMIT]:
            if line.startswith('+') and not line.startswith('+++'):
                print(f"{Colors.GREEN}{line}{Colors.RESET}")
            elif line.startswith('-') and not line.startswith('---'):
                print(f"{Colors.RED}{line}{Colors.RESET}")
            elif line.startswith('@@'):
                print(f"{Colors.CYAN}{line}{Colors.RESET}")
            else:
                print(f"{Colors.DIM}{line}{Colors.RESET}")

        if len(diff_lines) > LIMIT:
            remaining = len(diff_lines) - LIMIT
            print(f"\n{Colors.YELLOW}  ... {remaining} more lines not shown. Use Export (option 6) for the full diff.{Colors.RESET}")

        safe_input(f"\n{Colors.DIM}Press Enter to return to file list...{Colors.RESET}")


def cleanup_redundant_branches(repo_path: Path, target_branch: str = "main"):
    """
    Detect and cleanup redundant branches that are already merged or have no unique commits.
    Offers to cherry-pick if needed and delete both local and remote.
    """
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}BRANCH CLEANUP{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"Target branch: {Colors.CYAN}{target_branch}{Colors.RESET}")
    print()
    
    # Get all branches
    branches = list_branches(repo_path)
    current_branch = get_current_branch(repo_path)
    default_branch = get_default_branch(repo_path)

    # Never include the target, current, or default branch as candidates for deletion
    protected = {target_branch, current_branch, default_branch} - {None}
    local_branches = [b for b in branches['local'] if b not in protected]
    
    if not local_branches:
        print(f"{Colors.YELLOW}No branches to analyze{Colors.RESET}")
        return
    
    print(f"{Colors.BRIGHT_BLUE}Analyzing {len(local_branches)} branches...{Colors.RESET}\n")
    
    redundant = []
    has_changes = []
    
    for branch in local_branches:
        # Check if branch has commits not in target
        result = run_git(["rev-list", f"{target_branch}..{branch}"], repo_path, check=False)
        commits = result.stdout.strip().split('\n') if result.stdout.strip() else []
        
        if not commits or (len(commits) == 1 and not commits[0]):
            # No unique commits - redundant
            redundant.append((branch, []))
        else:
            # Has unique commits
            has_changes.append((branch, commits))
    
    # Show redundant branches
    if redundant:
        print(f"{Colors.BOLD}✅ Redundant branches (already merged/no changes):{Colors.RESET}")
        for i, (branch, _) in enumerate(redundant, 1):
            # Check if exists on remote
            remote_check = run_git(["ls-remote", "--heads", "origin", branch], repo_path, check=False)
            remote_marker = f" {Colors.DIM}[remote: ✓]{Colors.RESET}" if remote_check.stdout.strip() else ""
            print(f"  {i}. {branch}{remote_marker}")
        print()
    
    # Show branches with changes
    if has_changes:
        print(f"{Colors.BOLD}📋 Branches with unique commits:{Colors.RESET}")
        for i, (branch, commits) in enumerate(has_changes, 1):
            remote_check = run_git(["ls-remote", "--heads", "origin", branch], repo_path, check=False)
            remote_marker = f" {Colors.DIM}[remote: ✓]{Colors.RESET}" if remote_check.stdout.strip() else ""
            print(f"  {i}. {branch} ({len(commits)} commit{'s' if len(commits) > 1 else ''}){remote_marker}")
        print()
    
    # Offer cleanup options
    print(f"{Colors.BOLD}Cleanup options:{Colors.RESET}")
    print(f"  1. Delete all redundant branches (local + remote)")
    print(f"  2. Review and cherry-pick branches with changes")
    print(f"  3. Sync: Delete remote branches that don't exist locally")
    print(f"  4. Show detailed analysis for a specific branch")
    print(f"  5. Restore recently deleted branch from remote")
    print(f"  0. Back")
    
    choice = safe_input(f"\n{Colors.CYAN}Choose option:{Colors.RESET} ").strip()
    
    if choice == "1":
        # Delete redundant branches
        if not redundant:
            print(f"{Colors.YELLOW}No redundant branches to delete{Colors.RESET}")
            return
        
        print(f"\n{Colors.YELLOW}⚠️  This will delete {len(redundant)} redundant branch(es) locally and remotely{Colors.RESET}")
        confirm = safe_input("Continue? (yes/no): ").strip().lower()
        
        if confirm != "yes":
            print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
            return
        
        for branch, _ in redundant:
            # Safety: never delete current or default branch
            if branch in protected:
                print(f"  {Colors.YELLOW}⚠ Skipped '{branch}' (protected branch){Colors.RESET}")
                continue
            # Delete local
            delete_result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["branch", "-d", branch],
                description=f"delete redundant branch '{branch}'"
            )
            
            if delete_result.returncode == 0:
                print(f"  {Colors.GREEN}✓ Deleted local: {branch}{Colors.RESET}")
                
                # Check and delete remote
                remote_check = run_git(["ls-remote", "--heads", "origin", branch], repo_path, check=False)
                if remote_check.stdout.strip():
                    remote_result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["push", "origin", "--delete", "refs/heads/" + branch],
                        description=f"delete remote branch 'origin/{branch}'"
                    )
                    if remote_result.returncode == 0:
                        print(f"    {Colors.GREEN}✓ Deleted remote: origin/{branch}{Colors.RESET}")
                    else:
                        print(f"    {Colors.YELLOW}⚠️  Remote delete failed{Colors.RESET}")
            else:
                print(f"  {Colors.RED}✗ Failed to delete: {branch}{Colors.RESET}")
        
        print(f"\n{Colors.GREEN}✅ Cleanup complete!{Colors.RESET}")
    
    elif choice == "2":
        # Review branches with changes
        if not has_changes:
            print(f"{Colors.YELLOW}No branches with unique commits{Colors.RESET}")
            return
        
        for branch, commits in has_changes:
            print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
            print(f"{Colors.BOLD}Branch: {Colors.CYAN}{branch}{Colors.RESET} ({len(commits)} commit{'s' if len(commits) > 1 else ''}){Colors.RESET}")
            print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
            
            # Show commits
            for commit_sha in commits[:5]:  # Show first 5
                log_result = run_git(["log", "-1", "--oneline", commit_sha], repo_path, check=False)
                print(f"  + {log_result.stdout}")
            if len(commits) > 5:
                print(f"  ... and {len(commits) - 5} more")
            
            # Show diff stat
            print(f"\n{Colors.DIM}Changes:{Colors.RESET}")
            diff_stat = run_git(["diff", "--stat", f"{target_branch}...{branch}"], repo_path, check=False)
            print(diff_stat.stdout)
            
            print(f"\n{Colors.BOLD}Actions:{Colors.RESET}")
            print(f"  1. Cherry-pick to {target_branch}")
            print(f"  2. Skip this branch")
            print(f"  3. Delete this branch")
            print(f"  0. Stop reviewing")
            
            action = safe_input(f"\n{Colors.CYAN}Choose action:{Colors.RESET} ").strip()
            
            if action == "1":
                # Cherry-pick with smart commit message
                print(f"\n{Colors.BOLD}Preparing cherry-pick...{Colors.RESET}")
                
                # For single commit, use original message
                if len(commits) == 1:
                    original_msg = run_git(["log", "-1", "--format=%B", commits[0]], repo_path, check=False).stdout.strip()
                    print(f"\n{Colors.BOLD}Original commit message:{Colors.RESET}")
                    print(f"{Colors.DIM}{original_msg}{Colors.RESET}")
                else:
                    # For multiple commits, show a simple list
                    print(f"\n{Colors.BOLD}Will cherry-pick {len(commits)} commits:{Colors.RESET}")
                    for commit_sha in commits[:10]:
                        log_result = run_git(["log", "-1", "--oneline", commit_sha], repo_path, check=False)
                        print(f"  + {log_result.stdout}")
                    if len(commits) > 10:
                        print(f"  ... and {len(commits) - 10} more")
                
                confirm_pick = safe_input(f"\n{Colors.YELLOW}Proceed with cherry-pick? (y/n):{Colors.RESET} ").strip().lower()
                if confirm_pick == 'y':
                    # Perform cherry-pick
                    stashed = stash_ignored_changes(repo_path, f"Before cherry-pick {branch}")
                    
                    # Get revisions in order
                    revs = run_git(["rev-list", "--reverse", f"{target_branch}..{branch}"], repo_path, check=False).stdout.strip().split()
                    
                    cherry_result = run_git(["cherry-pick"] + revs, repo_path, check=False)
                    
                    if cherry_result.returncode == 0:
                        # For single commit, keep original message (git already did this)
                        if len(commits) == 1:
                            print(f"{Colors.GREEN}✅ Cherry-picked with original message{Colors.RESET}")
                        else:
                            # For multiple commits, git cherry-picks them individually, so leave as-is
                            print(f"{Colors.GREEN}✅ Cherry-picked {len(commits)} commits{Colors.RESET}")
                        
                        if stashed:
                            restore_latest_stash(repo_path)
                        
                        # Show what was applied
                        print(f"\n{Colors.BOLD}Changes applied:{Colors.RESET}")
                        show_result = run_git(["diff", "--stat", f"HEAD~{len(commits)}..HEAD"], repo_path, check=False)
                        print(show_result.stdout)
                        
                        # Offer to push and delete
                        push_choice = safe_input(f"\n{Colors.CYAN}Push to remote? (y/n):{Colors.RESET} ").strip().lower()
                        if push_choice == 'y':
                            push_result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["push", "origin", target_branch],
                                description=f"push {target_branch} after cherry-pick"
                            )
                            if push_result.returncode == 0:
                                print(f"{Colors.GREEN}✓ Pushed{Colors.RESET}")
                        
                        delete_choice = safe_input(f"\n{Colors.CYAN}Delete '{branch}' (local + remote)? (y/n):{Colors.RESET} ").strip().lower()
                        if delete_choice == 'y':
                            if branch in protected:
                                print(f"  {Colors.YELLOW}⚠ Cannot delete '{branch}' — it is a protected branch.{Colors.RESET}")
                            else:
                                delete_branch(repo_path, branch, force=True, delete_remote=True)
                    else:
                        # Check if it's an empty patch (already applied)
                        err_msg = cherry_result.stderr + cherry_result.stdout
                        is_empty = "The previous cherry-pick is now empty" in err_msg or \
                                   "allow-empty" in err_msg or \
                                   "git cherry-pick --skip" in err_msg
                        
                        if is_empty:
                            print(f"{Colors.YELLOW}💡 Patch is empty - changes already exist in {target_branch}{Colors.RESET}")
                            print(f"{Colors.DIM}   Skipping redundant commit...{Colors.RESET}")
                            
                            # Skip the cherry-pick
                            skip_result = run_git(["cherry-pick", "--skip"], repo_path, check=False)
                            
                            if stashed:
                                restore_latest_stash(repo_path)
                            
                            # Branch is redundant, offer to delete
                            print(f"\n{Colors.YELLOW}💡 Branch '{branch}' appears redundant (changes already in '{target_branch}'){Colors.RESET}")
                            if branch in protected:
                                print(f"  {Colors.DIM}(Skipping delete offer — '{branch}' is a protected branch){Colors.RESET}")
                            else:
                                delete_choice = safe_input(f"Delete '{branch}' (local + remote)? (y/n): ").strip().lower()
                                if delete_choice == 'y':
                                    delete_branch(repo_path, branch, force=True, delete_remote=True)
                        else:
                            # Real conflict
                            print(f"{Colors.RED}✗ Cherry-pick failed: {cherry_result.stderr}{Colors.RESET}")
                            print(f"\n{Colors.YELLOW}Please resolve conflicts manually:{Colors.RESET}")
                            print(f"  1. Fix conflicts in the files")
                            print(f"  2. Run: git add .")
                            print(f"  3. Run: git cherry-pick --continue")
                            print(f"  Or run: git cherry-pick --abort to cancel")
                            
                            if stashed:
                                print(f"\n{Colors.MAGENTA}📦 Background changes are stashed and will be auto-restored when done.{Colors.RESET}")
                            return  # Exit cleanup to let user handle it
            
            elif action == "3":
                # Delete branch
                if branch in protected:
                    print(f"  {Colors.YELLOW}⚠ Cannot delete '{branch}' — it is a protected branch (current or default).{Colors.RESET}")
                else:
                    delete_choice = safe_input(f"\n{Colors.YELLOW}Delete '{branch}' (local + remote)? (yes/no):{Colors.RESET} ").strip().lower()
                    if delete_choice == "yes":
                        delete_branch(repo_path, branch, force=True, delete_remote=True)
            
            elif action == "0":
                break
    
    elif choice == "3":
        # Sync: delete remote branches that don't exist locally
        print(f"\n{Colors.BRIGHT_BLUE}Checking for remote branches deleted locally...{Colors.RESET}")
        
        local_set = set(branches['local'])
        remote_result = run_git(["ls-remote", "--heads", "origin"], repo_path, check=False)
        remote_branches = []
        
        for line in remote_result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                branch_name = parts[1].replace('refs/heads/', '')
                remote_branches.append(branch_name)
        
        deleted = [b for b in remote_branches if b not in local_set]
        
        if not deleted:
            print(f"{Colors.GREEN}✅ All remote branches exist locally{Colors.RESET}")
        else:
            print(f"\n{Colors.BOLD}Found {len(deleted)} remote branch(es) deleted locally:{Colors.RESET}")
            for branch in deleted:
                print(f"  - {branch}")
            
            confirm = safe_input(f"\n{Colors.YELLOW}Delete these from remote? (yes/no):{Colors.RESET} ").strip().lower()
            if confirm == "yes":
                for branch in deleted:
                    result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["push", "origin", "--delete", "refs/heads/" + branch],
                        description=f"delete remote branch 'origin/{branch}'"
                    )
                    if result.returncode == 0:
                        print(f"  {Colors.GREEN}✓ {branch}{Colors.RESET}")
                    else:
                        print(f"  {Colors.RED}✗ {branch}{Colors.RESET}")
    
    elif choice == "4":
        # Detailed analysis
        all_branches = local_branches
        print(f"\n{Colors.BOLD}Select branch to analyze:{Colors.RESET}")
        for i, branch in enumerate(all_branches, 1):
            print(f"  {i}. {branch}")
        
        sel = safe_input(f"\n{Colors.CYAN}Enter number:{Colors.RESET} ").strip()
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(all_branches):
                branch = all_branches[idx]
                compare_branches_simple(repo_path, source=branch, target=target_branch)
    
    elif choice == "5":
        # Restore recently deleted branch from remote
        print(f"\n{Colors.BRIGHT_BLUE}Checking all remotes for deleted branches...{Colors.RESET}")
        
        # Get all remotes
        remotes_result = run_git(["remote"], repo_path, check=False)
        remotes = [r.strip() for r in remotes_result.stdout.strip().split('\n') if r.strip()]
        
        if not remotes:
            print(f"{Colors.YELLOW}No remotes configured{Colors.RESET}")
            return
        
        print(f"Found remotes: {', '.join(remotes)}")
        
        # Get local branches
        local_set = set(branches['local'])
        
        # Check each remote for branches
        remote_branches = {}  # {remote_name: [branches]}
        
        for remote in remotes:
            result = run_git(["ls-remote", "--heads", remote], repo_path, check=False)
            if result.returncode == 0:
                remote_branches[remote] = []
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                        branch_name = parts[1].replace('refs/heads/', '')
                        if branch_name not in local_set:
                            remote_branches[remote].append(branch_name)
        
        # Display available branches by remote
        all_options = []
        print(f"\n{Colors.BOLD}Branches available on remotes (not local):{Colors.RESET}")
        
        for remote, branch_list in remote_branches.items():
            if branch_list:
                print(f"\n  {Colors.CYAN}{remote}:{Colors.RESET}")
                for branch in branch_list:
                    all_options.append((remote, branch))
                    print(f"    {len(all_options)}. {branch}")
        
        if not all_options:
            print(f"{Colors.YELLOW}No remote branches to restore{Colors.RESET}")
            return
        
        sel = safe_input(f"\n{Colors.CYAN}Enter number to restore:{Colors.RESET} ").strip()
        
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(all_options):
                remote, branch_to_restore = all_options[idx]
                
                print(f"\n{Colors.CYAN}Restoring '{branch_to_restore}' from {remote}...{Colors.RESET}")
                
                # Create local tracking branch
                result = run_git(["branch", "--track", branch_to_restore, f"{remote}/{branch_to_restore}"], repo_path, check=False)
                
                if result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Restored branch '{branch_to_restore}' from {remote}{Colors.RESET}")
                    
                    # Offer to switch to it
                    switch_choice = safe_input(f"\n{Colors.CYAN}Switch to '{branch_to_restore}'? (y/n):{Colors.RESET} ").strip().lower()
                    if switch_choice == 'y':
                        switch_result = run_git(["checkout", branch_to_restore], repo_path, check=False)
                        if switch_result.returncode == 0:
                            print(f"{Colors.GREEN}✓ Switched to '{branch_to_restore}'{Colors.RESET}")
                            # Return immediately to exit cleanup and refresh the menu
                            return
                        else:
                            print(f"{Colors.RED}✗ Failed to switch: {switch_result.stderr.strip()}{Colors.RESET}")
                else:
                    print(f"{Colors.RED}✗ Failed to restore: {result.stderr.strip()}{Colors.RESET}")
            else:
                print(f"{Colors.RED}Invalid selection{Colors.RESET}")
        else:
            print(f"{Colors.RED}Invalid input{Colors.RESET}")


def show_branch_menu(repo_path: Path):
    """Interactive menu for branch operations."""
    signal.signal(signal.SIGINT, _sigint_handler)
    try:
        _show_branch_menu_inner(repo_path)
    except UserCancelled:
        print(f"\n\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
        sys.exit(0)


def _show_branch_menu_inner(repo_path: Path):
    """Interactive menu for branch operations — inner loop."""
    while True:
        current = get_current_branch(repo_path)
        default = get_default_branch(repo_path)
        branches = list_branches(repo_path)
        
        print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}BRANCH MANAGEMENT{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")
        print(f"Repository: {Colors.CYAN}{repo_path}{Colors.RESET}")
        print(f"Current Branch: {Colors.BRIGHT_GREEN}{current or 'detached HEAD'}{Colors.RESET}")
        print(f"Default Branch: {Colors.BRIGHT_CYAN}{default or 'unknown'}{Colors.RESET}")
        
        # Check upstream status for all local branches
        upstream_statuses = get_all_branches_upstream_status(repo_path, branches['local'])
        gone_branches = [b for b, s in upstream_statuses.items() if s['upstream_gone']]

        print(f"\n{Colors.BOLD}Local Branches:{Colors.RESET}")
        for branch in branches['local']:
            marker = f"{Colors.BRIGHT_GREEN}● {Colors.RESET}" if branch == current else "  "
            default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if branch == default else ""
            us = upstream_statuses.get(branch, {})
            if us.get('upstream_gone'):
                tracking = f" {Colors.RED}[upstream gone: {us['upstream']}]{Colors.RESET}"
            elif us.get('upstream'):
                tracking = f" {Colors.DIM}→ {us['upstream']}{Colors.RESET}"
            else:
                tracking = f" {Colors.DIM}(local only){Colors.RESET}"
            print(f"{marker}{branch}{default_marker}{tracking}")

        if gone_branches:
            print(f"\n{Colors.RED}⚠  {len(gone_branches)} branch(es) have a missing upstream — use option A to fix{Colors.RESET}")
        
        print(f"\n{Colors.BOLD}Available Operations:{Colors.RESET}")
        print("  1. Create new branch")
        print("  2. Switch branch")
        print("  3. Rename current branch")
        print("  4. Change default branch")
        print("  5. Delete branch")
        print("  6. List all branches (including remote)")
        print("  7. Manage remote branches")
        print("  8. Compare & Merge branches (Simple)")
        print("  9. Cleanup redundant branches")
        print(f"  {Colors.YELLOW}A. Fix upstream tracking{Colors.RESET}  (set/unset/repair branch → remote tracking)")
        print(f"  {Colors.CYAN}R. Manage remotes{Colors.RESET}  (add/view/remove remotes, fetch upstream fork)")
        print(f"  {Colors.YELLOW}S. Stash manager{Colors.RESET}  (list/apply/restore stashed changes)")
        print("  0. Exit")
        
        try:
            choice = safe_input(f"\n{Colors.BRIGHT_BLUE}Choose option (0-9, A, R, S):{Colors.RESET} ").strip().upper()
        except (KeyboardInterrupt, EOFError, UserCancelled):
            print(f"\n\n{Colors.YELLOW}Cancelled{Colors.RESET}")
            break
        
        if choice == "0":
            break
        
        elif choice == "9":
            # Cleanup redundant branches
            cleanup_redundant_branches(repo_path, default)
        
        elif choice == "1":
            # Create new branch
            branch_name = safe_input(f"{Colors.CYAN}Enter new branch name:{Colors.RESET} ").strip()
            if not branch_name:
                print(f"{Colors.RED}Branch name cannot be empty{Colors.RESET}")
                continue
            
            from_ref = safe_input(f"{Colors.CYAN}Create from (Enter for current HEAD):{Colors.RESET} ").strip()
            create_branch(repo_path, branch_name, from_ref if from_ref else None)
            
            switch = safe_input(f"{Colors.CYAN}Switch to new branch? (y/n):{Colors.RESET} ").strip().lower()
            if switch == 'y':
                switch_branch(repo_path, branch_name)
        
        elif choice == "2":
            # Switch branch
            print(f"\n{Colors.BOLD}Available branches:{Colors.RESET}")
            for i, branch in enumerate(branches['local'], 1):
                marker = f"{Colors.BRIGHT_GREEN}(current){Colors.RESET}" if branch == current else ""
                print(f"  {i}. {branch} {marker}")
            
            selection = safe_input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            
            if selection.isdigit():
                idx = int(selection) - 1
                if 0 <= idx < len(branches['local']):
                    switch_branch(repo_path, branches['local'][idx])
                else:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
            else:
                switch_branch(repo_path, selection)
        
        elif choice == "3":
            # Rename current branch
            if not current:
                print(f"{Colors.RED}Cannot rename - not on a branch (detached HEAD){Colors.RESET}")
                continue
            
            new_name = safe_input(f"{Colors.CYAN}Enter new name for '{current}':{Colors.RESET} ").strip()
            if not new_name:
                print(f"{Colors.RED}Branch name cannot be empty{Colors.RESET}")
                continue
            
            update_remote = safe_input(f"{Colors.CYAN}Update remote as well? (y/n):{Colors.RESET} ").strip().lower()
            rename_branch(repo_path, current, new_name, update_remote == 'y')
        
        elif choice == "4":
            # Change default branch
            print(f"\n{Colors.BOLD}Select new default branch:{Colors.RESET}")
            for i, branch in enumerate(branches['local'], 1):
                default_marker = f"{Colors.BRIGHT_CYAN}(current default){Colors.RESET}" if branch == default else ""
                print(f"  {i}. {branch} {default_marker}")
            
            selection = safe_input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            
            branch_name = None
            if selection.isdigit():
                idx = int(selection) - 1
                if 0 <= idx < len(branches['local']):
                    branch_name = branches['local'][idx]
                else:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                    continue
            else:
                branch_name = selection
            
            if branch_name:
                confirm = safe_input(f"{Colors.YELLOW}Set '{branch_name}' as default branch? (y/n):{Colors.RESET} ").strip().lower()
                if confirm == 'y':
                    change_default_branch(repo_path, branch_name)
        
        elif choice == "5":
            # Delete branch
            print(f"\n{Colors.BOLD}Select branch to delete:{Colors.RESET}")
            # Show ALL local branches — current branch is included but marked
            # confirm_and_delete_branch will offer a switch-away flow if needed
            deletable = branches['local']

            if not deletable:
                print(f"{Colors.YELLOW}No branches to delete{Colors.RESET}")
                continue

            for i, branch in enumerate(deletable, 1):
                current_marker = f" {Colors.BRIGHT_GREEN}(current){Colors.RESET}" if branch == current else ""
                default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if branch == default else ""
                print(f"  {i}. {branch}{current_marker}{default_marker}")

            selection = safe_input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()

            if not selection:
                print(f"{Colors.DIM}Cancelled.{Colors.RESET}")
                continue

            branch_name = None
            if selection.isdigit():
                idx = int(selection) - 1
                if 0 <= idx < len(deletable):
                    branch_name = deletable[idx]
                else:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                    continue
            else:
                if selection not in deletable:
                    print(f"{Colors.RED}Branch '{selection}' not found{Colors.RESET}")
                    continue
                branch_name = selection

            confirm_and_delete_branch(repo_path, branch_name, current)
        
        elif choice == "6":
            # List all branches
            print(f"\n{Colors.BOLD}LOCAL BRANCHES:{Colors.RESET}")
            for branch in branches['local']:
                marker = f"{Colors.BRIGHT_GREEN}● {Colors.RESET}" if branch == current else "  "
                default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if branch == default else ""
                print(f"{marker}{branch}{default_marker}")
            
            if branches['remote']:
                print(f"\n{Colors.BOLD}REMOTE BRANCHES:{Colors.RESET}")
                for branch in branches['remote']:
                    display = branch.replace('remotes/origin/', '')
                    print(f"  {display}")
            
            safe_input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")
        
        elif choice == "7":
            # Manage remote branches
            print(f"\n{Colors.BOLD}Remote Branch Management:{Colors.RESET}")
            print("  1. Fetch remote branches (update refs)")
            print("  2. Fetch ONE remote branch locally")
            print("  3. Fetch ALL remote branches locally")
            print("  4. Delete remote branch")
            print("  5. Prune stale remote branches")
            print("  6. Sync deletions to remote (delete remote branches deleted locally)")
            print("  7. Push local branch to remote")
            print(f"  {Colors.CYAN}8. Add upstream remote{Colors.RESET}  (track original/forked repo, fetch their tags)")
            
            remote_choice = safe_input(f"\n{Colors.CYAN}Choose option:{Colors.RESET} ").strip()
            
            if remote_choice == "1":
                # Fetch all remotes
                print(f"\n{Colors.BRIGHT_BLUE}Fetching from remote...{Colors.RESET}")
                result = run_git(["fetch", "--all", "--prune"], repo_path)
                if result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Fetched all remote branches{Colors.RESET}")
                    branches = list_branches(repo_path)
                else:
                    print(f"{Colors.RED}✗ Fetch failed: {result.stderr.strip()}{Colors.RESET}")
            
            elif remote_choice == "2":
                # Fetch specific remote branch locally
                print(f"\n{Colors.BRIGHT_BLUE}Fetching remote branches...{Colors.RESET}")
                run_git(["fetch", "--all", "--prune"], repo_path)
                branches = list_branches(repo_path)
                
                if not branches['remote']:
                    print(f"{Colors.YELLOW}No remote branches found{Colors.RESET}")
                    continue
                
                local_names = set(branches['local'])
                
                print(f"\n{Colors.BOLD}Remote branches not yet local:{Colors.RESET}")
                remote_only = []
                for branch in branches['remote']:
                    clean_name = branch.replace('remotes/origin/', '').replace('remotes/', '')
                    if 'HEAD' not in branch and clean_name not in local_names:
                        remote_only.append(clean_name)
                        print(f"  {len(remote_only)}. {clean_name}")
                
                if not remote_only:
                    print(f"{Colors.YELLOW}All remote branches already local{Colors.RESET}")
                    continue
                
                selection = safe_input(f"\n{Colors.CYAN}Enter number or name:{Colors.RESET} ").strip()
                
                branch_to_fetch = None
                if selection.isdigit():
                    idx = int(selection) - 1
                    if 0 <= idx < len(remote_only):
                        branch_to_fetch = remote_only[idx]
                else:
                    branch_to_fetch = selection
                
                if branch_to_fetch:
                    # Create local tracking branch WITHOUT switching
                    result = run_git(["branch", "--track", branch_to_fetch, f"origin/{branch_to_fetch}"], repo_path)
                    if result.returncode == 0:
                        print(f"{Colors.GREEN}✓ Fetched '{branch_to_fetch}' locally{Colors.RESET}")
                        
                        # Offer to switch
                        switch = safe_input(f"{Colors.CYAN}Switch to it now? (y/n):{Colors.RESET} ").strip().lower()
                        if switch == 'y':
                            switch_result = run_git(["checkout", branch_to_fetch], repo_path)
                            if switch_result.returncode == 0:
                                print(f"{Colors.GREEN}✓ Switched to '{branch_to_fetch}'{Colors.RESET}")
                                current = branch_to_fetch
                        
                        branches = list_branches(repo_path)
                    else:
                        print(f"{Colors.RED}✗ Failed: {result.stderr.strip()}{Colors.RESET}")
            
            elif remote_choice == "3":
                # Fetch ALL remote branches locally at once
                print(f"\n{Colors.BRIGHT_BLUE}Fetching remote branches...{Colors.RESET}")
                run_git(["fetch", "--all", "--prune"], repo_path)
                branches = list_branches(repo_path)
                
                if not branches['remote']:
                    print(f"{Colors.YELLOW}No remote branches found{Colors.RESET}")
                    continue
                
                local_names = set(branches['local'])
                
                # Find all remote branches not yet local, grouped by remote
                remote_branches_by_remote = {}  # {remote_name: [branch_name, ...]}
                
                for branch in branches['remote']:
                    if 'HEAD' not in branch:
                        # Parse: remotes/origin/main -> remote=origin, branch=main
                        # Parse: remotes/gitlab/dev -> remote=gitlab, branch=dev
                        parts = branch.replace('remotes/', '').split('/', 1)
                        if len(parts) == 2:
                            remote_name, branch_name = parts
                            if branch_name not in local_names:
                                if remote_name not in remote_branches_by_remote:
                                    remote_branches_by_remote[remote_name] = []
                                remote_branches_by_remote[remote_name].append(branch_name)
                
                if not remote_branches_by_remote:
                    print(f"{Colors.GREEN}✓ All remote branches already local{Colors.RESET}")
                    continue
                
                # Show grouped by remote
                total_count = sum(len(branches) for branches in remote_branches_by_remote.values())
                print(f"\n{Colors.BOLD}Found {total_count} remote branch(es) not yet local:{Colors.RESET}")
                
                for remote_name, branch_list in remote_branches_by_remote.items():
                    print(f"\n  {Colors.CYAN}From {remote_name}:{Colors.RESET}")
                    for branch_name in branch_list[:10]:
                        print(f"    - {branch_name}")
                    if len(branch_list) > 10:
                        print(f"    ... and {len(branch_list)-10} more")
                
                confirm = safe_input(f"\n{Colors.CYAN}Fetch ALL {total_count} branches locally? (y/n):{Colors.RESET} ").strip().lower()
                
                if confirm == 'y':
                    print(f"\n{Colors.BRIGHT_BLUE}Fetching {total_count} branches...{Colors.RESET}")
                    
                    success_count = 0
                    fail_count = 0
                    
                    for remote_name, branch_list in remote_branches_by_remote.items():
                        for branch_name in branch_list:
                            # Create tracking branch with correct remote
                            result = run_git(["branch", "--track", branch_name, f"{remote_name}/{branch_name}"], repo_path)
                            if result.returncode == 0:
                                success_count += 1
                                print(f"{Colors.DIM}  ✓ {branch_name} (from {remote_name}){Colors.RESET}")
                            else:
                                fail_count += 1
                                print(f"{Colors.RED}  ✗ {branch_name}: {result.stderr.strip()}{Colors.RESET}")
                    
                    print(f"\n{Colors.GREEN}✓ Fetched {success_count} branches locally{Colors.RESET}")
                    if fail_count > 0:
                        print(f"{Colors.YELLOW}⚠️  {fail_count} branches failed{Colors.RESET}")
                    
                    branches = list_branches(repo_path)
            
            elif remote_choice == "4":
                # Delete remote branch
                print(f"\n{Colors.BRIGHT_BLUE}Fetching remote branches...{Colors.RESET}")
                run_git(["fetch", "--all", "--prune"], repo_path)
                branches = list_branches(repo_path)
                
                if not branches['remote']:
                    print(f"{Colors.YELLOW}No remote branches{Colors.RESET}")
                    continue
                
                print(f"\n{Colors.BOLD}Remote branches:{Colors.RESET}")
                remote_branches = []
                for branch in branches['remote']:
                    clean_name = branch.replace('remotes/origin/', '').replace('remotes/', '')
                    if 'HEAD' not in branch:
                        remote_branches.append(clean_name)
                        print(f"  {len(remote_branches)}. {clean_name}")
                
                if not remote_branches:
                    print(f"{Colors.YELLOW}No branches to delete{Colors.RESET}")
                    continue
                
                selection = safe_input(f"\n{Colors.CYAN}Enter number or name to delete:{Colors.RESET} ").strip()
                
                branch_to_delete = None
                if selection.isdigit():
                    idx = int(selection) - 1
                    if 0 <= idx < len(remote_branches):
                        branch_to_delete = remote_branches[idx]
                else:
                    branch_to_delete = selection
                
                if branch_to_delete:
                    print(f"\n{Colors.YELLOW}⚠️  Delete origin/{branch_to_delete}?{Colors.RESET}")
                    confirm = safe_input(f"{Colors.CYAN}Confirm (y/n):{Colors.RESET} ").strip().lower()
                    
                    if confirm == 'y':
                        result = atomic_git_operation(
                            repo_path=repo_path,
                            git_command=["push", "origin", "--delete", "refs/heads/" + branch_to_delete],
                            description=f"delete remote branch 'origin/{branch_to_delete}'"
                        )
                        if result.returncode == 0:
                            print(f"{Colors.GREEN}✓ Deleted origin/{branch_to_delete}{Colors.RESET}")
                            branches = list_branches(repo_path)
                        else:
                            print(f"{Colors.RED}✗ Failed: {result.stderr.strip()}{Colors.RESET}")
            
            elif remote_choice == "5":
                # Prune stale branches
                print(f"\n{Colors.BRIGHT_BLUE}Pruning stale remote branches...{Colors.RESET}")
                result = run_git(["remote", "prune", "origin"], repo_path)
                if result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Pruned stale branches{Colors.RESET}")
                    if result.stdout.strip():
                        print(result.stdout)
                    branches = list_branches(repo_path)
                else:
                    print(f"{Colors.RED}✗ Prune failed: {result.stderr.strip()}{Colors.RESET}")
            
            elif remote_choice == "6":
                # Sync deletions to remote
                print(f"\n{Colors.BRIGHT_BLUE}Checking for branches deleted locally but still on remote...{Colors.RESET}")
                
                # Get local branches
                local_branches = set(branches['local'])
                
                # Get remote branches
                remote_result = run_git(["ls-remote", "--heads", "origin"], repo_path)
                remote_branches = []
                
                for line in remote_result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    # Parse "hash refs/heads/branch" format
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                        branch_name = parts[1].replace('refs/heads/', '')
                        remote_branches.append(branch_name)
                
                # Find branches on remote but not local
                deleted = [b for b in remote_branches if b not in local_branches]
                
                if not deleted:
                    print(f"{Colors.GREEN}✅ No branches to clean up - local and remote are in sync{Colors.RESET}")
                else:
                    print(f"\n{Colors.BOLD}Found {len(deleted)} branch(es) deleted locally but still on origin:{Colors.RESET}")
                    for i, branch_name in enumerate(deleted, 1):
                        print(f"  {i}. {branch_name}")
                    
                    print(f"\n{Colors.YELLOW}⚠️  This will DELETE these branches from origin{Colors.RESET}")
                    confirm = safe_input("Delete all listed branches from remote? (yes/no): ").strip().lower()
                    
                    if confirm == 'yes':
                        print(f"\n{Colors.BRIGHT_BLUE}Deleting {len(deleted)} branches from origin...{Colors.RESET}")
                        
                        success_count = 0
                        fail_count = 0
                        
                        for branch_name in deleted:
                            result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["push", "origin", "--delete", "refs/heads/" + branch_name],
                                description=f"delete remote branch 'origin/{branch_name}'"
                            )
                            
                            if result.returncode == 0:
                                success_count += 1
                                print(f"  {Colors.GREEN}✓ {branch_name}{Colors.RESET}")
                            else:
                                fail_count += 1
                                print(f"  {Colors.RED}✗ {branch_name}: {result.stderr.strip()}{Colors.RESET}")
                        
                        print(f"\n{Colors.GREEN}✅ Deleted {success_count} branches from origin{Colors.RESET}")
                        if fail_count > 0:
                            print(f"{Colors.YELLOW}⚠️  {fail_count} branches failed{Colors.RESET}")
                        
                        branches = list_branches(repo_path)
                    else:
                        print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
            
            elif remote_choice == "7":
                # Push local branch to remote
                print(f"\n{Colors.BOLD}Push local branch to remote:{Colors.RESET}")
                
                # Get list of remotes
                remotes_result = run_git(["remote"], repo_path, check=False)
                remotes = [r.strip() for r in remotes_result.stdout.strip().split('\n') if r.strip()]
                
                if not remotes:
                    print(f"{Colors.YELLOW}No remotes configured{Colors.RESET}")
                    continue
                
                # Select remote
                if len(remotes) == 1:
                    remote = remotes[0]
                    print(f"Using remote: {remote}")
                else:
                    print(f"\n{Colors.BOLD}Available remotes:{Colors.RESET}")
                    for i, r in enumerate(remotes, 1):
                        print(f"  {i}. {r}")
                    
                    remote_sel = safe_input(f"\n{Colors.CYAN}Select remote (default=origin):{Colors.RESET} ").strip()
                    if remote_sel.isdigit():
                        idx = int(remote_sel) - 1
                        if 0 <= idx < len(remotes):
                            remote = remotes[idx]
                        else:
                            print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                            continue
                    elif remote_sel:
                        remote = remote_sel
                    else:
                        remote = "origin" if "origin" in remotes else remotes[0]
                
                # Select branch to push
                print(f"\n{Colors.BOLD}Select branch to push:{Colors.RESET}")
                for i, branch in enumerate(branches['local'], 1):
                    marker = f" {Colors.BRIGHT_GREEN}(current){Colors.RESET}" if branch == current else ""
                    print(f"  {i}. {branch}{marker}")
                
                branch_sel = safe_input(f"\n{Colors.CYAN}Enter number (default=current branch):{Colors.RESET} ").strip()
                
                branch_to_push = None
                if branch_sel.isdigit():
                    idx = int(branch_sel) - 1
                    if 0 <= idx < len(branches['local']):
                        branch_to_push = branches['local'][idx]
                elif not branch_sel:
                    branch_to_push = current
                else:
                    branch_to_push = branch_sel
                
                if branch_to_push:
                    # Check if branch exists on remote
                    remote_check = run_git(["ls-remote", "--heads", remote, branch_to_push], repo_path, check=False)
                    exists_on_remote = bool(remote_check.stdout.strip())
                    
                    if exists_on_remote:
                        print(f"\n{Colors.YELLOW}Branch '{branch_to_push}' already exists on {remote}{Colors.RESET}")
                        force = safe_input(f"Force push? (y/n): ").strip().lower()
                        
                        if force == 'y':
                            result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["push", "--force-with-lease", remote, branch_to_push],
                                description=f"force push '{branch_to_push}' to {remote}"
                            )
                        else:
                            print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
                            continue
                    else:
                        # New branch - push with upstream tracking
                        print(f"\n{Colors.CYAN}Pushing '{branch_to_push}' to {remote}...{Colors.RESET}")
                        result = atomic_git_operation(
                            repo_path=repo_path,
                            git_command=["push", "-u", remote, branch_to_push],
                            description=f"push '{branch_to_push}' to {remote} with upstream tracking"
                        )
                    
                    if result.returncode == 0:
                        print(f"{Colors.GREEN}✓ Pushed '{branch_to_push}' to {remote}{Colors.RESET}")
                        branches = list_branches(repo_path)
                    else:
                        print(f"{Colors.RED}✗ Failed: {result.stderr.strip()}{Colors.RESET}")
            
            elif remote_choice == "8":
                add_upstream_remote(repo_path)
        
        elif choice == "A":
            # Fix upstream tracking
            print(f"\n{Colors.BOLD}Select branch to fix:{Colors.RESET}")
            # Show all branches with their upstream status
            for i, b in enumerate(branches['local'], 1):
                us = upstream_statuses.get(b, {})
                if us.get('upstream_gone'):
                    tag = f" {Colors.RED}[upstream GONE: {us['upstream']}]{Colors.RESET}"
                elif us.get('upstream'):
                    tag = f" {Colors.DIM}→ {us['upstream']}{Colors.RESET}"
                else:
                    tag = f" {Colors.DIM}(local only — no upstream){Colors.RESET}"
                marker = f"{Colors.BRIGHT_GREEN}(current){Colors.RESET} " if b == current else ""
                print(f"  {i}. {marker}{b}{tag}")
            
            sel = safe_input(f"\n{Colors.CYAN}Enter number or name (Enter for current branch):{Colors.RESET} ").strip()
            
            if not sel:
                branch_to_fix = current
            elif sel.isdigit():
                idx = int(sel) - 1
                branch_to_fix = branches['local'][idx] if 0 <= idx < len(branches['local']) else None
            else:
                branch_to_fix = sel
            
            if branch_to_fix and branch_to_fix in branches['local']:
                fix_upstream_tracking(repo_path, branch_to_fix, upstream_statuses.get(branch_to_fix, {'upstream': None, 'upstream_gone': False}))
            else:
                print(f"{Colors.RED}Invalid selection{Colors.RESET}")

        elif choice == "R":
            # Manage remotes
            while True:
                print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
                print(f"{Colors.BOLD}MANAGE REMOTES{Colors.RESET}")
                print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")

                # List current remotes with URLs
                remotes_res = run_git(["remote", "-v"], repo_path)
                remotes_raw = {}
                for line in remotes_res.stdout.strip().splitlines():
                    parts = line.split()
                    if parts and parts[0] not in remotes_raw:
                        remotes_raw[parts[0]] = parts[1] if len(parts) > 1 else ""

                if remotes_raw:
                    print(f"\n{Colors.BOLD}Current remotes:{Colors.RESET}")
                    for rname, rurl in remotes_raw.items():
                        print(f"  {Colors.CYAN}{rname}{Colors.RESET}  {Colors.DIM}{rurl}{Colors.RESET}")
                else:
                    print(f"\n  {Colors.YELLOW}No remotes configured{Colors.RESET}")

                print(f"\n  1. Add remote  (add fork/upstream/mirror, search GitHub by name)")
                print(f"  2. Remove remote")
                print(f"  3. Rename remote")
                print(f"  4. Fetch from remote  (update refs + tags)")
                print(f"  0. Back")

                r_choice = safe_input(f"\n{Colors.CYAN}Choose:{Colors.RESET} ").strip()

                if r_choice == "0" or not r_choice:
                    break

                elif r_choice == "1":
                    add_upstream_remote(repo_path)

                elif r_choice == "2":
                    if not remotes_raw:
                        print(f"{Colors.YELLOW}No remotes to remove{Colors.RESET}")
                        continue
                    remote_list = list(remotes_raw.keys())
                    for i, r in enumerate(remote_list, 1):
                        print(f"  {i}. {r}  {Colors.DIM}{remotes_raw[r]}{Colors.RESET}")
                    sel = safe_input(f"\n{Colors.CYAN}Select remote to remove:{Colors.RESET} ").strip()
                    target_remote = None
                    if sel.isdigit():
                        idx = int(sel) - 1
                        if 0 <= idx < len(remote_list):
                            target_remote = remote_list[idx]
                    elif sel in remotes_raw:
                        target_remote = sel
                    if target_remote:
                        confirm = safe_input(f"{Colors.YELLOW}Remove remote '{target_remote}'? (y/n):{Colors.RESET} ").strip().lower()
                        if confirm == 'y':
                            res = run_git(["remote", "remove", target_remote], repo_path)
                            if res.returncode == 0:
                                print(f"{Colors.GREEN}✓ Removed remote '{target_remote}'{Colors.RESET}")
                            else:
                                print(f"{Colors.RED}✗ Failed: {res.stderr.strip()}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}Invalid selection{Colors.RESET}")

                elif r_choice == "3":
                    if not remotes_raw:
                        print(f"{Colors.YELLOW}No remotes to rename{Colors.RESET}")
                        continue
                    remote_list = list(remotes_raw.keys())
                    for i, r in enumerate(remote_list, 1):
                        print(f"  {i}. {r}")
                    sel = safe_input(f"\n{Colors.CYAN}Select remote to rename:{Colors.RESET} ").strip()
                    target_remote = None
                    if sel.isdigit():
                        idx = int(sel) - 1
                        if 0 <= idx < len(remote_list):
                            target_remote = remote_list[idx]
                    elif sel in remotes_raw:
                        target_remote = sel
                    if target_remote:
                        new_name = safe_input(f"{Colors.CYAN}New name for '{target_remote}':{Colors.RESET} ").strip()
                        if new_name:
                            res = run_git(["remote", "rename", target_remote, new_name], repo_path)
                            if res.returncode == 0:
                                print(f"{Colors.GREEN}✓ Renamed '{target_remote}' → '{new_name}'{Colors.RESET}")
                            else:
                                print(f"{Colors.RED}✗ Failed: {res.stderr.strip()}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}Invalid selection{Colors.RESET}")

                elif r_choice == "4":
                    if not remotes_raw:
                        print(f"{Colors.YELLOW}No remotes to fetch from{Colors.RESET}")
                        continue
                    remote_list = list(remotes_raw.keys())
                    for i, r in enumerate(remote_list, 1):
                        print(f"  {i}. {r}")
                    sel = safe_input(f"\n{Colors.CYAN}Select remote to fetch (Enter for all):{Colors.RESET} ").strip()
                    if not sel:
                        print(f"\n{Colors.BRIGHT_BLUE}Fetching from all remotes...{Colors.RESET}")
                        res = run_git(["fetch", "--all", "--tags", "--prune"], repo_path)
                    else:
                        target_remote = None
                        if sel.isdigit():
                            idx = int(sel) - 1
                            if 0 <= idx < len(remote_list):
                                target_remote = remote_list[idx]
                        elif sel in remotes_raw:
                            target_remote = sel
                        if not target_remote:
                            print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                            continue
                        print(f"\n{Colors.BRIGHT_BLUE}Fetching from '{target_remote}'...{Colors.RESET}")
                        res = run_git(["fetch", target_remote, "--tags", "--prune"], repo_path)
                    if res.returncode == 0:
                        print(f"{Colors.GREEN}✓ Fetch complete{Colors.RESET}")
                        # Show tag preview
                        tags_res = run_git(["tag", "--list", "--sort=-version:refname"], repo_path)
                        tags = [t.strip() for t in tags_res.stdout.strip().splitlines() if t.strip()]
                        if tags:
                            preview = ", ".join(tags[:6])
                            more = f" (+{len(tags)-6} more)" if len(tags) > 6 else ""
                            print(f"  {Colors.DIM}Tags: {preview}{more}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}✗ Fetch failed: {res.stderr.strip()}{Colors.RESET}")

        elif choice == "S":
            try:
                from gitship.stash import run_stash_menu
            except ImportError:
                # fallback: same dir
                import importlib.util, os
                _spec = importlib.util.spec_from_file_location("stash", os.path.join(os.path.dirname(__file__), "stash.py"))
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                run_stash_menu = _mod.run_stash_menu
            run_stash_menu(repo_path)

        elif choice == "8":
            # Compare branches - Simple Version
            while True:
                all_branches = branches['local'].copy()
                print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
                print(f"{Colors.BOLD}COMPARE BRANCHES{Colors.RESET}")
                print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
                
                # Show available branches
                for i, branch in enumerate(all_branches, 1):
                    marker = f" {Colors.BRIGHT_GREEN}(current){Colors.RESET}" if branch == current else ""
                    print(f"  {i}. {branch}{marker}")
                
                # Get Source Branch
                print(f"\n{Colors.DIM}Step 1: Select the SOURCE branch (The one containing the patch/feature){Colors.RESET}")
                b1_sel = safe_input(f"{Colors.CYAN}Source branch (number/name, 'b' for back):{Colors.RESET} ").strip()
                if b1_sel.lower() == 'b':
                    break
                
                branch1 = None
                if b1_sel.isdigit():
                    idx = int(b1_sel) - 1
                    if 0 <= idx < len(all_branches):
                        branch1 = all_branches[idx]
                else:
                    branch1 = b1_sel
                
                if not branch1:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                    continue
                
                # Get Target Branch
                default_target = current if current != branch1 else default
                print(f"\n{Colors.DIM}Step 2: Select the TARGET branch (Where you want to merge/apply the changes){Colors.RESET}")
                b2_sel = safe_input(f"{Colors.CYAN}Target branch (default={Colors.BRIGHT_GREEN}{default_target}{Colors.RESET}):{Colors.RESET} ").strip()
                
                if not b2_sel:
                    branch2 = default_target
                elif b2_sel.isdigit():
                    idx = int(b2_sel) - 1
                    if 0 <= idx < len(all_branches):
                        branch2 = all_branches[idx]
                else:
                    branch2 = b2_sel
                
                if not branch2:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                    continue
                
                # Run the simplified comparison
                # branch1 is SOURCE, branch2 is TARGET
                compare_branches_simple(repo_path, source=branch1, target=branch2)


def main_with_repo(repo_path: Path):
    """Main entry point when called from interactive menu."""
    show_branch_menu(repo_path)


def main_with_args(repo_path: str, operation: str = None, **kwargs):
    """Main entry point when called with CLI arguments."""
    repo = Path(repo_path)
    
    if operation == "list":
        branches = list_branches(repo)
        current = branches['current']
        default = get_default_branch(repo)
        
        print(f"\n{Colors.BOLD}LOCAL BRANCHES:{Colors.RESET}")
        for branch in branches['local']:
            marker = f"{Colors.BRIGHT_GREEN}● {Colors.RESET}" if branch == current else "  "
            default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if branch == default else ""
            print(f"{marker}{branch}{default_marker}")
        
        if kwargs.get('show_remote'):
            print(f"\n{Colors.BOLD}REMOTE BRANCHES:{Colors.RESET}")
            for branch in branches['remote']:
                display = branch.replace('remotes/origin/', '')
                print(f"  {display}")
    
    elif operation == "create":
        branch_name = kwargs.get('name')
        from_ref = kwargs.get('from_ref')
        create_branch(repo, branch_name, from_ref)
        
        if kwargs.get('switch'):
            switch_branch(repo, branch_name)
    
    elif operation == "switch":
        branch_name = kwargs.get('name')
        switch_branch(repo, branch_name)
    
    elif operation == "rename":
        old_name = kwargs.get('old_name') or get_current_branch(repo)
        new_name = kwargs.get('new_name')
        update_remote = kwargs.get('update_remote', False)
        rename_branch(repo, old_name, new_name, update_remote)
    
    elif operation == "delete":
        branch_name = kwargs.get('name')
        force = kwargs.get('force', False)
        delete_branch(repo, branch_name, force)
    
    elif operation == "set-default":
        branch_name = kwargs.get('name')
        change_default_branch(repo, branch_name)
    
    else:
        # No operation specified, show interactive menu
        show_branch_menu(repo)


def main():
    """Standalone entry point."""
    if len(sys.argv) < 2:
        show_branch_menu(Path.cwd())
    else:
        # Simple CLI parsing
        operation = sys.argv[1]
        main_with_args(str(Path.cwd()), operation)


if __name__ == "__main__":
    main()