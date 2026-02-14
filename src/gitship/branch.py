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
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple


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
        check=check
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
        print(f"{Colors.RED}✗ Failed to switch branch: {result.stderr.strip()}{Colors.RESET}")
        return False


def rename_branch(repo_path: Path, old_name: str, new_name: str, update_remote: bool = False) -> bool:
    """Rename a branch locally and optionally on remote."""
    # Check if we're on the branch to rename
    current = get_current_branch(repo_path)
    
    if current == old_name:
        # Rename current branch
        result = run_git(["branch", "-m", new_name], repo_path)
    else:
        # Rename another branch
        result = run_git(["branch", "-m", old_name, new_name], repo_path)
    
    if result.returncode != 0:
        print(f"{Colors.RED}✗ Failed to rename branch: {result.stderr.strip()}{Colors.RESET}")
        return False
    
    print(f"{Colors.GREEN}✓ Renamed local branch '{old_name}' → '{new_name}'{Colors.RESET}")
    
    if update_remote:
        # Push new branch to remote
        print(f"\n{Colors.CYAN}Updating remote...{Colors.RESET}")
        result = run_git(["push", "origin", new_name], repo_path)
        
        if result.returncode != 0:
            print(f"{Colors.YELLOW}⚠ Failed to push new branch to remote{Colors.RESET}")
        else:
            print(f"{Colors.GREEN}✓ Pushed '{new_name}' to remote{Colors.RESET}")
            
            # Delete old branch from remote
            result = run_git(["push", "origin", "--delete", old_name], repo_path)
            if result.returncode == 0:
                print(f"{Colors.GREEN}✓ Deleted '{old_name}' from remote{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠ Failed to delete old branch from remote{Colors.RESET}")
    
    return True


def delete_branch(repo_path: Path, branch_name: str, force: bool = False) -> bool:
    """Delete a branch."""
    flag = "-D" if force else "-d"
    result = run_git(["branch", flag, branch_name], repo_path)
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Deleted branch '{branch_name}'{Colors.RESET}")
        return True
    else:
        print(f"{Colors.RED}✗ Failed to delete branch: {result.stderr.strip()}{Colors.RESET}")
        if not force and "not fully merged" in result.stderr:
            print(f"{Colors.YELLOW}💡 Use force delete if you're sure (will lose unmerged changes){Colors.RESET}")
        return False


def change_default_branch(repo_path: Path, new_default: str) -> bool:
    """Change the default branch for the repository."""
    print(f"\n{Colors.BOLD}Changing Default Branch to '{new_default}'{Colors.RESET}")
    print("=" * 60)
    
    # Step 1: Verify branch exists
    result = run_git(["rev-parse", "--verify", f"refs/heads/{new_default}"], repo_path)
    if result.returncode != 0:
        print(f"{Colors.RED}✗ Branch '{new_default}' does not exist locally{Colors.RESET}")
        return False
    
    # Step 2: Push branch to remote if needed
    print(f"\n1. Ensuring '{new_default}' exists on remote...")
    result = run_git(["push", "-u", "origin", new_default], repo_path)
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Branch pushed/updated on remote{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ Warning: Could not push to remote{Colors.RESET}")
    
    # Step 3: Update symbolic ref on remote
    print(f"\n2. Updating remote default branch...")
    result = run_git(["symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{new_default}"], repo_path)
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Updated local tracking of remote default{Colors.RESET}")
    
    # Step 4: Instructions for GitHub/GitLab
    print(f"\n{Colors.BRIGHT_CYAN}3. Update default branch on hosting platform:{Colors.RESET}")
    print(f"   {Colors.DIM}GitHub:{Colors.RESET} Settings → Branches → Default branch → Switch to '{new_default}'")
    print(f"   {Colors.DIM}GitLab:{Colors.RESET} Settings → Repository → Default Branch → Select '{new_default}'")
    print(f"   {Colors.DIM}Manual:{Colors.RESET} git remote set-head origin {new_default}")
    
    print(f"\n{Colors.GREEN}✓ Local configuration updated!{Colors.RESET}")
    print(f"{Colors.YELLOW}⚠ Don't forget to update the default branch on your remote hosting platform{Colors.RESET}")
    
    return True


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
        print("  0. Exit")
        
        try:
            choice = input(f"\n{Colors.BRIGHT_BLUE}Choose option (0-6):{Colors.RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.YELLOW}Cancelled{Colors.RESET}")
            break
        
        if choice == "0":
            break
        
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
            delete_branch(repo_path, branch_name, force == 'y')
        
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
                    # Clean up the display
                    display = branch.replace('remotes/origin/', '')
                    print(f"  {display}")
            
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")
        


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