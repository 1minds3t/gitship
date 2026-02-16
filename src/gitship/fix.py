#!/usr/bin/env python3
"""
fixgit - Selective file restorer from Git commit history.

Allows you to restore specific files to their state before a problematic commit,
with interactive file selection and safe atomic operations.
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional
import logging

try:
    from gitship.gitops import atomic_git_operation, stash_ignored_changes, restore_latest_stash
except ImportError:
    # Fallback if gitops isn't available
    atomic_git_operation = None
    stash_ignored_changes = None
    restore_latest_stash = None


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
        try:
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
        except Exception:
            # If logging setup fails, continue without file logging
            pass
    
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
                   capture_output: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)


def get_branch_name(repo_path: Path) -> str:
    """Get the current branch name."""
    result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else "main"


def stop_autopush_service(logger: GitCraftLogger) -> bool:
    """Stop autopush service if it exists and is active."""
    service_name = "autopush-stealth.service"
    
    try:
        # Check if service exists
        result = subprocess.run(
            ["systemctl", "list-units", "--full", "--all"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if service_name not in result.stdout:
            return False
        
        # Check if active
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:  # Active
            logger.info("Stopping autopush service temporarily...")
            result = subprocess.run(
                ["sudo", "systemctl", "stop", service_name],
                capture_output=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.error("Failed to stop autopush service")
                return False
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # systemctl not available or timeout
        pass
    
    return False


def restart_autopush_service(was_active: bool, logger: GitCraftLogger):
    """Restart autopush service if it was active before."""
    if was_active:
        service_name = "autopush-stealth.service"
        logger.info("Restarting autopush service...")
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "start", service_name],
                capture_output=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.error("Failed to restart autopush service. Please check manually.")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.error("Failed to restart autopush service. Please check manually.")


def get_parent_commit(repo_path: Path, commit_sha: str, logger: GitCraftLogger) -> str:
    """Get the parent commit of the given commit."""
    result = run_git_command(["rev-parse", f"{commit_sha}^"], cwd=repo_path)
    
    if result.returncode != 0:
        logger.error(f"Failed to get parent of {commit_sha}")
        sys.exit(1)
    
    return result.stdout.strip()


def get_changed_files(repo_path: Path, commit_sha: str, logger: GitCraftLogger) -> List[str]:
    """Get list of files changed in the commit."""
    result = run_git_command([
        "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha
    ], cwd=repo_path)
    
    if result.returncode != 0:
        logger.error(f"Failed to get changed files for {commit_sha}")
        sys.exit(1)
    
    files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    return files


def restore_file(repo_path: Path, file_path: str, parent_sha: str, logger: GitCraftLogger) -> bool:
    """Restore a single file to its state at parent commit."""
    # Check if file exists at parent
    result = run_git_command(["show", f"{parent_sha}:{file_path}"], cwd=repo_path)
    
    if result.returncode != 0:
        logger.error(f"File {file_path} does not exist in commit {parent_sha}")
        return False
    
    # Restore file
    full_path = repo_path / file_path
    logger.info(f"Restoring {file_path} to its state at {parent_sha[:8]}...")
    
    try:
        # Ensure parent directories exist
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(result.stdout)
    except Exception as e:
        logger.error(f"Failed to restore {file_path}: {e}")
        return False
    
    # Stage the file
    result = run_git_command(["add", file_path], cwd=repo_path)
    if result.returncode != 0:
        logger.error(f"Failed to stage {file_path}")
        return False
    
    return True


def main_with_args(repo_path_str: str, commit_sha: str):
    """Main function that can be called programmatically."""
    repo_path = Path(repo_path_str).resolve()
    logger = GitCraftLogger("git-fix")
    
    logger.info(f"Starting git-fix for {repo_path} on branch {get_branch_name(repo_path)}")
    
    # Validate repository
    if not (repo_path / ".git").exists():
        logger.error(f"Repository path invalid or not a Git repository: {repo_path}")
        sys.exit(1)
    
    os.chdir(repo_path)
    
    # Handle autopush service
    autopush_was_active = stop_autopush_service(logger)
    
    try:
        # Validate commit SHA
        result = run_git_command(["rev-parse", commit_sha], cwd=repo_path)
        if result.returncode != 0:
            logger.error(f"Invalid commit SHA: {commit_sha}")
            sys.exit(1)
        
        # Get parent commit
        parent_sha = get_parent_commit(repo_path, commit_sha, logger)
        logger.info(f"Using parent commit: {parent_sha[:8]}")
        
        # Get changed files
        changed_files = get_changed_files(repo_path, commit_sha, logger)
        
        if not changed_files:
            logger.error(f"No files changed in commit {commit_sha}")
            sys.exit(1)
        
        # Display files
        print(f"Files changed in commit {commit_sha}:")
        for i, file_path in enumerate(changed_files, 1):
            print(f"{i}. {file_path}")
        
        # Get user selection
        print("\nEnter the number(s) of the file(s) to restore (e.g., '1' or '1 2 3', or 'all' for all files):")
        selection = input().strip()
        
        # Process selection
        selected_files = []
        if selection.lower() == "all":
            selected_files = changed_files
        else:
            try:
                selected_nums = [int(n.strip()) for n in selection.split()]
                for num in selected_nums:
                    if 1 <= num <= len(changed_files):
                        selected_files.append(changed_files[num - 1])
                    else:
                        logger.error(f"Invalid file number: {num}")
            except ValueError:
                logger.error("Invalid input format")
                sys.exit(1)
        
        if not selected_files:
            logger.error("No valid files selected")
            sys.exit(1)
        
        # Restore files
        restored_count = 0
        for file_path in selected_files:
            if restore_file(repo_path, file_path, parent_sha, logger):
                restored_count += 1
        
        if restored_count == 0:
            logger.info("No files were restored")
            return
        
        # Check if there are changes to commit
        result = run_git_command(["diff", "--cached", "--quiet"], cwd=repo_path)
        if result.returncode == 0:
            logger.info("No changes to commit after restoring files")
            return
        
        # Commit changes
        files_str = '", "'.join(selected_files)
        commit_message = f'Revert "{files_str}" to state before {commit_sha} (parent: {parent_sha[:8]})'
        
        if atomic_git_operation:
            result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["commit", "-m", commit_message, "--quiet"],
                description=f"commit revert of files from {commit_sha}"
            )
        else:
            result = run_git_command(["commit", "-m", commit_message, "--quiet"], cwd=repo_path)
        
        if result.returncode != 0:
            logger.error(f"Failed to commit changes for {selected_files}")
            sys.exit(1)
        
        logger.info(f'Committed changes: "{commit_message}"')
        
        # Push changes
        branch = get_branch_name(repo_path)
        logger.info(f"Pushing new commit to origin/{branch}...")
        
        if atomic_git_operation:
            result = atomic_git_operation(
                repo_path=repo_path,
                git_command=["push", "origin", f"HEAD:{branch}", "--quiet"],
                description=f"push revert to origin/{branch}"
            )
        else:
            result = run_git_command(["push", "origin", f"HEAD:{branch}", "--quiet"], cwd=repo_path)
        
        if result.returncode != 0:
            logger.error(f"Failed to push new commit to origin/{branch}")
        else:
            logger.info(f"Successfully pushed new commit to origin/{branch}")
        
        logger.info(f"git-fix completed. File(s) {', '.join(selected_files)} restored to state before {commit_sha}")
        
    finally:
        # Always restart autopush if it was active
        restart_autopush_service(autopush_was_active, logger)


def main_with_repo(repo_path: Path):
    """Interactive mode for menu integration - prompts for commit SHA."""
    print("Enter the commit SHA to revert to the state BEFORE this commit:")
    commit_sha = input().strip()
    
    if not commit_sha:
        print("ERROR: No commit SHA provided", file=sys.stderr)
        sys.exit(1)
    
    main_with_args(str(repo_path), commit_sha)


def main():
    """Main entry point for fixgit."""
    # Auto-detect repo or use provided path
    repo_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    
    # Get commit SHA from args or prompt
    if len(sys.argv) > 2:
        commit_sha = sys.argv[2]
    else:
        print("Enter the commit SHA to revert to the state BEFORE this commit:")
        commit_sha = input().strip()
    
    if not commit_sha:
        print("ERROR: No commit SHA provided", file=sys.stderr)
        sys.exit(1)
    
    main_with_args(str(repo_path), commit_sha)


if __name__ == "__main__":
    main()