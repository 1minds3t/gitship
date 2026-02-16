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
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

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
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if result.returncode == 0:
        return result.stdout.strip()
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
            
            choice = input("\nChoice (1-4): ").strip()
            
            if choice == '1':
                # Stash and switch
                print("\n📦 Stashing changes...")
                stash_result = run_git(["stash", "push", "-m", f"Auto-stash before switching to {branch_name}"], repo_path)
                if stash_result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Changes stashed{Colors.RESET}")
                    # Try switch again
                    switch_result = run_git(["checkout", branch_name], repo_path)
                    if switch_result.returncode == 0:
                        print(f"{Colors.GREEN}✓ Switched to branch '{branch_name}'{Colors.RESET}")
                        print(f"\n💡 To restore your stashed changes later, run: git stash pop")
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
                confirm = input("Type 'yes' to confirm: ").strip().lower()
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

def verify_and_offer_delete(repo_path: Path, source: str, target: str):
    """
    Verify if source changes are present in target (via hash comparison)
    and offer to delete the source branch.
    """
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

    # Offer delete
    print(f"\n{Colors.BOLD}Branch Cleanup:{Colors.RESET}")
    print(f"The branch '{Colors.CYAN}{source}{Colors.RESET}' appears fully synced/redundant.")
    choice = input(f"{Colors.YELLOW}Delete branch '{source}'? (y/n):{Colors.RESET} ").strip().lower()
    
    if choice == 'y':
        # Safety: ensure we aren't deleting current branch (we should be on target)
        current = get_current_branch(repo_path)
        if current == source:
            print(f"{Colors.RED}✗ Cannot delete current branch. Switch to {target} first.{Colors.RESET}")
            return

        res = run_git(["branch", "-D", source], repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✓ Deleted branch {source}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Failed to delete: {res.stderr.strip()}{Colors.RESET}")
    else:
        print("Branch kept.")

def rename_branch(repo_path: Path, old_name: str, new_name: str, update_remote: bool = False) -> bool:
    """Rename a branch locally and optionally on remote using atomic operations."""
    current = get_current_branch(repo_path)
    
    if current == old_name:
        result = run_git(["branch", "-m", new_name], repo_path)
    else:
        result = run_git(["branch", "-m", old_name, new_name], repo_path)
    
    if result.returncode != 0:
        print(f"{Colors.RED}✗ Failed to rename branch: {result.stderr.strip()}{Colors.RESET}")
        return False
    
    print(f"{Colors.GREEN}✓ Renamed local branch '{old_name}' → '{new_name}'{Colors.RESET}")
    
    if update_remote:
        print(f"\n{Colors.CYAN}Updating remote...{Colors.RESET}")
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=["push", "origin", new_name],
            description=f"push renamed branch '{new_name}' to remote"
        )
        
        if result.returncode != 0:
            print(f"{Colors.YELLOW}⚠ Failed to push new branch to remote{Colors.RESET}")
        else:
            print(f"{Colors.GREEN}✓ Pushed '{new_name}' to remote{Colors.RESET}")
            result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", "origin", "--delete", old_name],
                description=f"delete old remote branch '{old_name}'"
            )
            if result.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted '{old_name}' from remote{Colors.RESET}")
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
                git_command=["push", "origin", "--delete", branch_name],
                description=f"delete remote branch 'origin/{branch_name}'"
            )
            
            if push_result.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted remote branch 'origin/{branch_name}'{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  Failed to delete remote branch: {push_result.stderr.strip()}{Colors.RESET}")
                print(f"{Colors.DIM}   You can manually delete it with: git push origin --delete {branch_name}{Colors.RESET}")
        elif remote_exists:
            print(f"{Colors.DIM}ℹ️  Remote branch 'origin/{branch_name}' still exists{Colors.RESET}")
            print(f"{Colors.DIM}   Delete it with: git push origin --delete {branch_name}{Colors.RESET}")
        
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
            
            choice = input(f"\n{Colors.YELLOW}Abort the stuck {name} and reset to clean state? (y/n):{Colors.RESET} ").strip().lower()
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




# =============================================================================
# SIMPLE COMPARISON & MERGE LOGIC
# =============================================================================

def merge_branches_interactive(repo_path: Path, source: str, target: str):
    """Merge source into target interactively."""
    print(f"\n{Colors.BOLD}🔀 MERGE: {Colors.CYAN}{source}{Colors.RESET} → {Colors.CYAN}{target}{Colors.RESET}")
    print(f"⚠️  This will:")
    print(f"   1. Stash ignorable background changes (atomic)")
    print(f"   2. Check out '{target}'")
    print(f"   3. Merge '{source}' into it")
    
    confirm = input(f"\n{Colors.YELLOW}Continue? (y/n):{Colors.RESET} ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        return
    
    # 0. Atomic Stash
    stashed = stash_ignored_changes(repo_path, f"Before merge {source} into {target}")

    # 1. Switch
    print(f"\n{Colors.DIM}[1/2] Switching to {target}...{Colors.RESET}")
    res_checkout = run_git(["checkout", target], repo_path)
    if res_checkout.returncode != 0:
        print(f"{Colors.RED}❌ Failed to switch branches: {res_checkout.stderr.strip()}{Colors.RESET}")
        if stashed:
            print(f"{Colors.YELLOW}⚠️  Stash kept. Restoring now...{Colors.RESET}")
            restore_latest_stash(repo_path)
        return
    
    # 2. Merge with detailed message
    print(f"{Colors.DIM}[2/2] Merging {source}...{Colors.RESET}")
    
    # Generate detailed merge message
    try:
        from gitship.merge_message import generate_merge_message
        
        # Get the merge base to use as base_ref
        merge_base_result = run_git(["merge-base", target, source], repo_path, check=False)
        if merge_base_result.returncode == 0:
            base_ref = merge_base_result.stdout.strip()
        else:
            base_ref = target
        
        merge_msg = generate_merge_message(
            repo_path=repo_path,
            base_ref=base_ref,
            head_ref=source
        )
        
        # Perform merge with custom message
        res_merge = run_git(["merge", "--no-ff", "-m", merge_msg, source], repo_path, check=False)
    except ImportError:
        # Fallback to default merge if merge_message not available
        res_merge = run_git(["merge", source], repo_path, check=False)
    
    if res_merge.returncode != 0:
        print(f"\n{Colors.RED}❌ Merge failed with conflicts.{Colors.RESET}")
        print(f"   Git output: {res_merge.stdout} {res_merge.stderr}")
        print(f"\n{Colors.YELLOW}ACTION REQUIRED:{Colors.RESET}")
        print(f"   1. Open files with conflicts")
        print(f"   2. Fix them")
        print(f"   3. Run: git add .")
        print(f"   4. Run: git commit")
        if stashed:
            print(f"\n{Colors.MAGENTA}📦 Note: Ignorable files are stashed. Run 'git stash pop' AFTER you finish the merge.{Colors.RESET}")
        return
    else:
        print(f"\n{Colors.GREEN}✅ Merge successful!{Colors.RESET}")
        
        # Restore stash if success
        if stashed:
            restore_latest_stash(repo_path)
            
        # Verify and Cleanup
        verify_and_offer_delete(repo_path, source, target)
        
        # 3. Optional Push
        do_push = input(f"\n{Colors.CYAN}🚀 Push updated {target} to remote? (y/n):{Colors.RESET} ").strip().lower()
        if do_push == 'y':
            print(f"{Colors.DIM}Pushing...{Colors.RESET}")
            res_push = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", "origin", target],
                description=f"push {target} after merge"
            )
            if res_push.returncode == 0:
                print(f"{Colors.GREEN}✓ Pushed to origin/{target}{Colors.RESET}")
            else:
                print(f"{Colors.RED}✗ Push failed: {res_push.stderr.strip()}{Colors.RESET}")


def export_comparison(repo_path: Path, branch1: str, branch2: str, commits_1: List[str], commits_2: List[str]):
    """Export comparison to file."""
    try:
        from gitship.config import load_config
        config = load_config()
        # Default to a generic export location if config fails
        export_dir = Path(config.get('export_path', Path.home() / 'gitship_exports'))
    except ImportError:
        export_dir = Path.home() / 'gitship_exports'
    
    export_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"compare_{branch1}_vs_{branch2}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = export_dir / filename
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("="*60 + "\n")
        f.write(f"BRANCH COMPARISON: {branch1} vs {branch2}\n")
        f.write(f"Repository: {repo_path}\n")
        f.write(f"Generated: {datetime.now()}\n")
        f.write("="*60 + "\n\n")
        
        f.write(f"Commits ONLY in {branch1} ({len(commits_1)}):\n")
        f.write("-" * 60 + "\n")
        for line in commits_1:
            f.write(f"{line}\n")
        if not commits_1:
            f.write("(None)\n")
        f.write("\n")
        
        f.write(f"Commits ONLY in {branch2} ({len(commits_2)}):\n")
        f.write("-" * 60 + "\n")
        for line in commits_2:
            f.write(f"{line}\n")
        if not commits_2:
            f.write("(None)\n")
        f.write("\n")
    
    print(f"{Colors.GREEN}✅ Exported to: {filepath}{Colors.RESET}")


def compare_branches_simple(repo_path: Path, source: str, target: str):
    """Show directional comparison: Source -> Target."""
    
    # Check for broken state before doing anything
    if not ensure_clean_git_state(repo_path):
        print(f"\n{Colors.RED}Cannot proceed with review while git state is interrupted.{Colors.RESET}")
        return

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
    print(f"  3. View full diff (content changes)")
    print(f"  4. Swap Source/Target (Review other direction)")
    print(f"  5. Export this comparison")
    print(f"  0. Back")
    
    choice = input(f"\n{Colors.BLUE}Choice (0-5):{Colors.RESET} ").strip()
    
    if choice == "1":
        merge_branches_interactive(repo_path, source=source, target=target)
    elif choice == "2":
        print(f"\n{Colors.BOLD}🍒 CHERRY-PICK PREVIEW{Colors.RESET}")
        print(f"This will apply {len(incoming_list)} commit(s) from {Colors.CYAN}{source}{Colors.RESET} to {Colors.CYAN}{target}{Colors.RESET}")
        print(f"\n{Colors.BOLD}Commits to apply:{Colors.RESET}")
        for commit_sha in incoming_list[:10]:  # Show first 10
            log_result = run_git(["log", "-1", "--oneline", commit_sha], repo_path)
            print(f"  + {log_result.stdout.strip()}")
        if len(incoming_list) > 10:
            print(f"  ... and {len(incoming_list) - 10} more")
        
        print(f"\n{Colors.BOLD}Files that will change:{Colors.RESET}")
        diff_stat = run_git(["diff", "--stat", f"{target}...{source}"], repo_path)
        print(diff_stat.stdout)
        
        confirm = input(f"\n{Colors.YELLOW}Proceed with cherry-pick? (y/n):{Colors.RESET} ").strip().lower()
        if confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
            return
        
        print(f"\n{Colors.BOLD}🍒 Cherry-picking {len(incoming_list)} commits from {source} INTO {target}...{Colors.RESET}")
        
        # 0. Atomic Stash
        stashed = stash_ignored_changes(repo_path, f"Before cherry-pick {source} into {target}")

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
        
        # Get revisions in chronological order (oldest first)
        revs = run_git(["rev-list", "--reverse", f"{target}..{source}"], repo_path).stdout.strip().split()
        if not revs:
            print("No commits to pick.")
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
            
            # Offer to push
            push_choice = input(f"\n{Colors.CYAN}Push to remote? (y/n):{Colors.RESET} ").strip().lower()
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
            
            is_empty = "The previous cherry-pick is now empty" in err_msg or \
                       "allow-empty" in err_msg or \
                       "git cherry-pick --skip" in err_msg
            
            if is_empty:
                print(f"{Colors.YELLOW}💡 Patch is empty or already exists in target.{Colors.RESET}")
                print(f"{Colors.DIM}   Skipping redundant commit...{Colors.RESET}")
                
                skip_res = run_git(["cherry-pick", "--skip"], repo_path)
                
                if skip_res.returncode == 0:
                    print(f"{Colors.GREEN}✅ Successfully synced (skipped redundant patches).{Colors.RESET}")
                    if stashed:
                        restore_latest_stash(repo_path)
                    
                    # Offer to delete the now-redundant source branch
                    print(f"\n{Colors.YELLOW}💡 Branch '{source}' is now redundant (changes already in '{target}'){Colors.RESET}")
                    delete_offer = input(f"Delete branch '{source}'? (y/n): ").strip().lower()
                    
                    if delete_offer == 'y':
                        # Check if we're on the source branch
                        current_branch_res = run_git(["branch", "--show-current"], repo_path)
                        current_branch = current_branch_res.stdout.strip()
                        
                        if current_branch == source:
                            print(f"{Colors.YELLOW}⚠️  Currently on '{source}', switching to '{target}' first...{Colors.RESET}")
                            switch_res = run_git(["checkout", target], repo_path)
                            if switch_res.returncode != 0:
                                print(f"{Colors.RED}✗ Failed to switch branches{Colors.RESET}")
                                return
                        
                        # Delete local branch
                        delete_res = run_git(["branch", "-d", source], repo_path)
                        if delete_res.returncode == 0:
                            print(f"{Colors.GREEN}✓ Deleted local branch '{source}'{Colors.RESET}")
                            
                            # Offer to delete remote too
                            delete_remote = input(f"Also delete remote branch 'origin/{source}'? (y/n): ").strip().lower()
                            if delete_remote == 'y':
                                remote_del = run_git(["push", "origin", "--delete", source], repo_path)
                                if remote_del.returncode == 0:
                                    print(f"{Colors.GREEN}✓ Deleted remote branch 'origin/{source}'{Colors.RESET}")
                                else:
                                    print(f"{Colors.YELLOW}⚠️  Remote delete failed (may not exist): {remote_del.stderr.strip()}{Colors.RESET}")
                        else:
                            print(f"{Colors.RED}✗ Delete failed: {delete_res.stderr.strip()}{Colors.RESET}")
                    
                    return
                else:
                    print(f"{Colors.RED}✗ Failed to skip: {skip_res.stderr}{Colors.RESET}")

            # If not empty (or skip failed), it's a real conflict
            print(f"{Colors.RED}❌ Cherry-pick encountered conflicts.{Colors.RESET}")
            print(res.stderr)
            print(f"\n{Colors.YELLOW}Fix conflicts manually, then run 'git cherry-pick --continue'{Colors.RESET}")
            if stashed:
                print(f"\n{Colors.MAGENTA}📦 Note: Ignorable files are stashed. Run 'git stash pop' AFTER you finish resolving conflicts.{Colors.RESET}")
    
    elif choice == "3":
        print(f"\n{Colors.BOLD}📄 FULL DIFF ({source} vs {target}):{Colors.RESET}")
        print("="*60)
        # Use 3-dot diff to see what source adds to target, force color for readability
        diff_res = run_git(["diff", "--color=always", f"{target}...{source}"], repo_path)
        if diff_res.stdout.strip():
            print(diff_res.stdout)
        else:
            print("(No content changes)")
        print("="*60)
        input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")
        # Re-show the menu
        compare_branches_simple(repo_path, source, target)

    elif choice == "4":
        # Recursively call with swapped args
        compare_branches_simple(repo_path, source=target, target=source)
    elif choice == "5":
        # We need to reconstruct the list for export
        export_comparison(repo_path, source, target, incoming_list, [])

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
    local_branches = [b for b in branches['local'] if b != target_branch]
    
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
    
    choice = input(f"\n{Colors.CYAN}Choose option:{Colors.RESET} ").strip()
    
    if choice == "1":
        # Delete redundant branches
        if not redundant:
            print(f"{Colors.YELLOW}No redundant branches to delete{Colors.RESET}")
            return
        
        print(f"\n{Colors.YELLOW}⚠️  This will delete {len(redundant)} redundant branch(es) locally and remotely{Colors.RESET}")
        confirm = input("Continue? (yes/no): ").strip().lower()
        
        if confirm != "yes":
            print(f"{Colors.YELLOW}Cancelled{Colors.RESET}")
            return
        
        for branch, _ in redundant:
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
                        git_command=["push", "origin", "--delete", branch],
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
            
            action = input(f"\n{Colors.CYAN}Choose action:{Colors.RESET} ").strip()
            
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
                
                confirm_pick = input(f"\n{Colors.YELLOW}Proceed with cherry-pick? (y/n):{Colors.RESET} ").strip().lower()
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
                        push_choice = input(f"\n{Colors.CYAN}Push to remote? (y/n):{Colors.RESET} ").strip().lower()
                        if push_choice == 'y':
                            push_result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["push", "origin", target_branch],
                                description=f"push {target_branch} after cherry-pick"
                            )
                            if push_result.returncode == 0:
                                print(f"{Colors.GREEN}✓ Pushed{Colors.RESET}")
                        
                        delete_choice = input(f"\n{Colors.CYAN}Delete '{branch}' (local + remote)? (y/n):{Colors.RESET} ").strip().lower()
                        if delete_choice == 'y':
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
                            delete_choice = input(f"Delete '{branch}' (local + remote)? (y/n): ").strip().lower()
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
                                print(f"\n{Colors.MAGENTA}📦 Note: Stashed changes exist. Run 'git stash pop' after resolving.{Colors.RESET}")
                            return  # Exit cleanup to let user handle it
            
            elif action == "3":
                # Delete branch
                delete_choice = input(f"\n{Colors.YELLOW}Delete '{branch}' (local + remote)? (yes/no):{Colors.RESET} ").strip().lower()
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
            
            confirm = input(f"\n{Colors.YELLOW}Delete these from remote? (yes/no):{Colors.RESET} ").strip().lower()
            if confirm == "yes":
                for branch in deleted:
                    result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["push", "origin", "--delete", branch],
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
        
        sel = input(f"\n{Colors.CYAN}Enter number:{Colors.RESET} ").strip()
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
        
        sel = input(f"\n{Colors.CYAN}Enter number to restore:{Colors.RESET} ").strip()
        
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
                    switch_choice = input(f"\n{Colors.CYAN}Switch to '{branch_to_restore}'? (y/n):{Colors.RESET} ").strip().lower()
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
        
        print(f"\n{Colors.BOLD}Local Branches:{Colors.RESET}")
        for branch in branches['local']:
            marker = f"{Colors.BRIGHT_GREEN}● {Colors.RESET}" if branch == current else "  "
            default_marker = f" {Colors.BRIGHT_CYAN}(default){Colors.RESET}" if branch == default else ""
            print(f"{marker}{branch}{default_marker}")
        
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
        print("  0. Exit")
        
        try:
            choice = input(f"\n{Colors.BRIGHT_BLUE}Choose option (0-9):{Colors.RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.YELLOW}Cancelled{Colors.RESET}")
            break
        
        if choice == "0":
            break
        
        elif choice == "9":
            # Cleanup redundant branches
            cleanup_redundant_branches(repo_path, default)
        
        elif choice == "1":
            # Create new branch
            branch_name = input(f"{Colors.CYAN}Enter new branch name:{Colors.RESET} ").strip()
            if not branch_name:
                print(f"{Colors.RED}Branch name cannot be empty{Colors.RESET}")
                continue
            
            from_ref = input(f"{Colors.CYAN}Create from (Enter for current HEAD):{Colors.RESET} ").strip()
            create_branch(repo_path, branch_name, from_ref if from_ref else None)
            
            switch = input(f"{Colors.CYAN}Switch to new branch? (y/n):{Colors.RESET} ").strip().lower()
            if switch == 'y':
                switch_branch(repo_path, branch_name)
        
        elif choice == "2":
            # Switch branch
            print(f"\n{Colors.BOLD}Available branches:{Colors.RESET}")
            for i, branch in enumerate(branches['local'], 1):
                marker = f"{Colors.BRIGHT_GREEN}(current){Colors.RESET}" if branch == current else ""
                print(f"  {i}. {branch} {marker}")
            
            selection = input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            
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
            
            new_name = input(f"{Colors.CYAN}Enter new name for '{current}':{Colors.RESET} ").strip()
            if not new_name:
                print(f"{Colors.RED}Branch name cannot be empty{Colors.RESET}")
                continue
            
            update_remote = input(f"{Colors.CYAN}Update remote as well? (y/n):{Colors.RESET} ").strip().lower()
            rename_branch(repo_path, current, new_name, update_remote == 'y')
        
        elif choice == "4":
            # Change default branch
            print(f"\n{Colors.BOLD}Select new default branch:{Colors.RESET}")
            for i, branch in enumerate(branches['local'], 1):
                default_marker = f"{Colors.BRIGHT_CYAN}(current default){Colors.RESET}" if branch == default else ""
                print(f"  {i}. {branch} {default_marker}")
            
            selection = input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            
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
                confirm = input(f"{Colors.YELLOW}Set '{branch_name}' as default branch? (y/n):{Colors.RESET} ").strip().lower()
                if confirm == 'y':
                    change_default_branch(repo_path, branch_name)
        
        elif choice == "5":
            # Delete branch
            print(f"\n{Colors.BOLD}Select branch to delete:{Colors.RESET}")
            deletable = [b for b in branches['local'] if b != current]
            
            if not deletable:
                print(f"{Colors.YELLOW}No other branches to delete{Colors.RESET}")
                continue
            
            for i, branch in enumerate(deletable, 1):
                print(f"  {i}. {branch}")
            
            selection = input(f"\n{Colors.CYAN}Enter number or branch name:{Colors.RESET} ").strip()
            
            branch_name = None
            if selection.isdigit():
                idx = int(selection) - 1
                if 0 <= idx < len(deletable):
                    branch_name = deletable[idx]
                else:
                    print(f"{Colors.RED}Invalid selection{Colors.RESET}")
                    continue
            else:
                branch_name = selection
            
            if branch_name == current:
                print(f"{Colors.RED}Cannot delete current branch{Colors.RESET}")
                continue
            
            force = input(f"{Colors.YELLOW}Force delete (may lose unmerged changes)? (y/n):{Colors.RESET} ").strip().lower()
            
            # Check if branch exists on remote
            check_remote = run_git(["ls-remote", "--heads", "origin", branch_name], repo_path, check=False)
            delete_remote = False
            
            if check_remote.returncode == 0 and check_remote.stdout.strip():
                delete_remote_input = input(f"{Colors.CYAN}Also delete from remote? (y/n):{Colors.RESET} ").strip().lower()
                delete_remote = (delete_remote_input == 'y')
            
            delete_branch(repo_path, branch_name, force == 'y', delete_remote)
        
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
            
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")
        
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
            
            remote_choice = input(f"\n{Colors.CYAN}Choose option:{Colors.RESET} ").strip()
            
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
                
                selection = input(f"\n{Colors.CYAN}Enter number or name:{Colors.RESET} ").strip()
                
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
                        switch = input(f"{Colors.CYAN}Switch to it now? (y/n):{Colors.RESET} ").strip().lower()
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
                
                confirm = input(f"\n{Colors.CYAN}Fetch ALL {total_count} branches locally? (y/n):{Colors.RESET} ").strip().lower()
                
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
                
                selection = input(f"\n{Colors.CYAN}Enter number or name to delete:{Colors.RESET} ").strip()
                
                branch_to_delete = None
                if selection.isdigit():
                    idx = int(selection) - 1
                    if 0 <= idx < len(remote_branches):
                        branch_to_delete = remote_branches[idx]
                else:
                    branch_to_delete = selection
                
                if branch_to_delete:
                    print(f"\n{Colors.YELLOW}⚠️  Delete origin/{branch_to_delete}?{Colors.RESET}")
                    confirm = input(f"{Colors.CYAN}Confirm (y/n):{Colors.RESET} ").strip().lower()
                    
                    if confirm == 'y':
                        result = atomic_git_operation(
                            repo_path=repo_path,
                            git_command=["push", "origin", "--delete", branch_to_delete],
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
                    confirm = input("Delete all listed branches from remote? (yes/no): ").strip().lower()
                    
                    if confirm == 'yes':
                        print(f"\n{Colors.BRIGHT_BLUE}Deleting {len(deleted)} branches from origin...{Colors.RESET}")
                        
                        success_count = 0
                        fail_count = 0
                        
                        for branch_name in deleted:
                            result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["push", "origin", "--delete", branch_name],
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
                    
                    remote_sel = input(f"\n{Colors.CYAN}Select remote (default=origin):{Colors.RESET} ").strip()
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
                
                branch_sel = input(f"\n{Colors.CYAN}Enter number (default=current branch):{Colors.RESET} ").strip()
                
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
                        force = input(f"Force push? (y/n): ").strip().lower()
                        
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
                b1_sel = input(f"{Colors.CYAN}Source branch (number/name, 'b' for back):{Colors.RESET} ").strip()
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
                b2_sel = input(f"{Colors.CYAN}Target branch (default={Colors.BRIGHT_GREEN}{default_target}{Colors.RESET}):{Colors.RESET} ").strip()
                
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