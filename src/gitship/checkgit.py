#!/usr/bin/env python3
"""
checkgit - Interactive Git commit inspector and reverter.

Displays the last 10 commits in a repository, allows inspection of file changes,
and provides an interactive workflow for reverting commits.
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple


class GitCraftLogger:
    """Simple logger for gitcraft operations."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        # Try /var/log first, fall back to /tmp
        log_dir = Path("/var/log")
        if not log_dir.exists() or not os.access(log_dir, os.W_OK):
            log_dir = Path("/tmp")
        
        log_file = log_dir / f"{name}.log"
        error_file = log_dir / f"{name}_errors.log"
        
        # File handlers
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.INFO)
        
        eh = logging.FileHandler(error_file)
        eh.setLevel(logging.ERROR)
        
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(message)s', 
                                     datefmt='%Y-%m-%d %H:%M:%S')
        fh.setFormatter(formatter)
        eh.setFormatter(formatter)
        
        self.logger.addHandler(fh)
        self.logger.addHandler(eh)
    
    def info(self, message: str):
        """Log info message."""
        self.logger.info(message)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")
    
    def error(self, message: str):
        """Log error message."""
        self.logger.error(message)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - ERROR: {message}", 
              file=sys.stderr)


def run_git_command(args: List[str], cwd: Optional[Path] = None, 
                   capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            check=False
        )
        return result
    except Exception as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)


def is_git_repo(path: Path) -> bool:
    """Check if the given path is a git repository."""
    result = run_git_command(["rev-parse", "--git-dir"], cwd=path)
    return result.returncode == 0


def get_branch_name(repo_path: Path) -> str:
    """Get the current branch name."""
    result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def get_last_commits(repo_path: Path, count: int = 10) -> List[str]:
    """Get the last N commits formatted for display."""
    result = run_git_command([
        "log", "--oneline", f"-n{count}",
        "--pretty=format:%h - %ar - %s (%an)"
    ], cwd=repo_path)
    
    if result.returncode != 0:
        return []
    
    return [line.strip() for line in result.stdout.strip().split('\n') if line]


def get_commit_hashes(repo_path: Path, count: int = 10) -> List[str]:
    """Get the last N commit hashes."""
    result = run_git_command([
        "log", "--pretty=format:%H", f"-n{count}"
    ], cwd=repo_path)
    
    if result.returncode != 0:
        return []
    
    return [h.strip() for h in result.stdout.strip().split('\n') if h]


def show_commit_files(repo_path: Path, commit_hash: str):
    """Show files changed in a commit."""
    print(f"\n=== Files changed in commit {commit_hash[:7]} ===")
    result = run_git_command(["show", "--name-status", commit_hash], cwd=repo_path)
    
    if result.returncode == 0:
        for line in result.stdout.split('\n'):
            if line and len(line) > 0 and line[0] in 'AMD':
                print(line)


def show_commit_diff(repo_path: Path, commit_hash: str):
    """Show the diff stats for a commit."""
    print(f"\n=== Commit diff preview ===")
    result = run_git_command(["show", "--stat", commit_hash], cwd=repo_path)
    if result.returncode == 0:
        print(result.stdout)


def call_fixgit(repo_path: Path, commit_hash: str, logger: GitCraftLogger):
    """Call the fixgit command."""
    try:
        # Try to import fixgit module
        from . import fixgit as fixgit_module
        logger.info(f"User initiated revert of commit {commit_hash}")
        fixgit_module.main_with_args(str(repo_path), commit_hash)
    except ImportError:
        logger.error("fixgit module not found")
        print("Error: fixgit is not available. Please ensure gitcraft is properly installed.")
        sys.exit(1)


def main():
    """Main entry point for checkgit."""
    # Auto-detect repo or use provided path
    repo_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    
    # Initialize logger
    logger = GitCraftLogger("checkgit")
    
    # Validate repo
    if not is_git_repo(repo_path):
        logger.error(f"Not in a git repository: {repo_path}")
        print(f"Error: Not in a git repository: {repo_path}")
        sys.exit(1)
    
    logger.info(f"Checking last 10 commits in {repo_path}")
    
    # Get branch name
    branch = get_branch_name(repo_path)
    
    # Display commits
    print()
    print(f"=== Last 10 commits in {repo_path.name} (branch: {branch}) ===")
    print()
    
    commits_display = get_last_commits(repo_path)
    for i, commit_line in enumerate(commits_display, 1):
        print(f"{i:2d}\t{commit_line}")
    
    print()
    print()
    
    # Get commit hashes for processing
    commit_hashes = get_commit_hashes(repo_path)
    
    if not commit_hashes:
        logger.error("No commits found.")
        print("No recent commits found.")
        sys.exit(1)
    
    # Show detailed view option
    show_details = input("Show detailed file changes for any commit? (y/n): ").strip().lower()
    
    if show_details in ('y', 'yes'):
        try:
            detail_num = int(input("Enter commit number (1-10): ").strip())
            
            if 1 <= detail_num <= len(commit_hashes):
                selected_hash = commit_hashes[detail_num - 1]
                show_commit_files(repo_path, selected_hash)
                show_commit_diff(repo_path, selected_hash)
        except (ValueError, IndexError):
            print("Invalid selection.")
    
    # Prompt for revert
    print()
    revert_choice = input("Revert any commit? (y/n): ").strip().lower()
    
    if revert_choice in ('y', 'yes'):
        try:
            commit_num = int(input("Enter the commit number to revert (1-10): ").strip())
            
            if 1 <= commit_num <= len(commit_hashes):
                selected_hash = commit_hashes[commit_num - 1]
                commit_short = selected_hash[:7]
                
                # Get commit message
                result = run_git_command([
                    "log", "-1", "--pretty=format:%s", selected_hash
                ], cwd=repo_path)
                commit_message = result.stdout.strip() if result.returncode == 0 else "Unknown"
                
                print()
                print("=== Revert Confirmation ===")
                print(f"Commit: {commit_short}")
                print(f"Message: {commit_message}")
                print("Files that will be affected:")
                
                # Show files
                result = run_git_command([
                    "diff-tree", "--no-commit-id", "--name-only", "-r", selected_hash
                ], cwd=repo_path)
                
                if result.returncode == 0:
                    for f in result.stdout.strip().split('\n'):
                        if f:
                            print(f"  - {f}")
                
                print()
                confirm = input(f"Confirm revert of commit {commit_short}? (y/n): ").strip().lower()
                
                if confirm in ('y', 'yes'):
                    print(f"Reverting commit {commit_short}...")
                    call_fixgit(repo_path, selected_hash, logger)
                else:
                    logger.info("User cancelled revert operation")
                    print("Revert cancelled.")
            else:
                logger.error(f"Invalid commit number: {commit_num}")
                print(f"Invalid selection. Please enter a number between 1 and {len(commit_hashes)}.")
                sys.exit(1)
        except ValueError:
            print("Invalid input. Please enter a number.")
            sys.exit(1)
    else:
        logger.info("No revert requested")
        print("No changes made. Goodbye!")


if __name__ == "__main__":
    main()