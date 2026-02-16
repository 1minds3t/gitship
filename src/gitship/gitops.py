#!/usr/bin/env python3
"""
gitops - Atomic git operations with intelligent change filtering.

Handles stashing of ignorable changes (translations, generated files, etc.)
around git operations to prevent race conditions with background processes.
"""

import subprocess
import fnmatch
import json as json_module
from pathlib import Path
from typing import List, Optional, Dict, Any


def run_git(args: list, cwd: Path = None, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run git command and return result with timeout and error handling."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=timeout,
            encoding='utf-8',
            errors='replace'
        )
        
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, 
                ["git"] + args,
                result.stdout,
                result.stderr
            )
        
        return result
        
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Git command timed out after {timeout}s: git {' '.join(args)}")
    except subprocess.CalledProcessError:
        raise
    except Exception as e:
        raise RuntimeError(f"Git command failed: git {' '.join(args)}\n{str(e)}")


def get_default_ignore_patterns() -> List[str]:
    """Get default patterns for files to ignore during git operations."""
    return [
        "*.po",      # Translation files
        "*.mo",      # Compiled translations
    ]


def get_ignore_patterns(repo_path: Path) -> List[str]:
    """Get ignore patterns for this project from config."""
    from gitship.config import load_config
    
    config = load_config()
    project_key = str(repo_path.resolve())
    
    # Get project-specific patterns, or use defaults
    project_patterns = config.get('project_ignore_patterns', {})
    patterns = project_patterns.get(project_key, get_default_ignore_patterns())
    
    return patterns


def has_ignored_changes(repo_path: Path, patterns: Optional[List[str]] = None) -> bool:
    """
    Check if there are uncommitted changes matching ignore patterns.
    
    Args:
        repo_path: Path to git repository
        patterns: List of file patterns to check (e.g., ["*.po", "*.mo"])
                  If None, uses project config or defaults
    
    Returns:
        True if there are uncommitted changes matching the patterns
    """
    if patterns is None:
        patterns = get_ignore_patterns(repo_path)
    
    if not patterns:
        return False
    
    # Get all uncommitted files
    result = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return False
    
    changed_files = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        # Parse git status output (format: "XY filename")
        parts = line.split(None, 1)
        if len(parts) == 2:
            changed_files.append(parts[1].strip())
    
    # Check if any changed files match ignore patterns using fnmatch
    for filepath in changed_files:
        for pattern in patterns:
            # Use fnmatch for reliable pattern matching
            if fnmatch.fnmatch(filepath, pattern) or fnmatch.fnmatch(Path(filepath).name, pattern):
                return True
    
    return False


def atomic_git_operation(
    repo_path: Path,
    git_command: List[str],
    description: str,
    ignore_patterns: Optional[List[str]] = None,
    verbose: bool = False,
    dry_run: bool = False,
    json_output: bool = False
) -> subprocess.CompletedProcess | Dict[str, Any]:
    """
    Atomically stash ignorable changes, run git command, then restore.
    
    This prevents race conditions where background processes (AI translators,
    code generators, etc.) write changes between stash and git operation.
    
    Args:
        repo_path: Path to git repository
        git_command: Git command args (e.g., ["push", "origin", "main"])
        description: Human-readable description for stash message
        ignore_patterns: File patterns to stash (e.g., ["*.po", "*.mo"])
                        If None, uses project config or defaults
        verbose: Print debug information
        dry_run: Simulate operation without executing
        json_output: Return JSON dict instead of CompletedProcess
    
    Returns:
        CompletedProcess from the git command, or dict if json_output=True
    """
    if ignore_patterns is None:
        ignore_patterns = get_ignore_patterns(repo_path)
    
    if verbose:
        print(f"\n[DEBUG] atomic_git_operation: {description}")
        print(f"[DEBUG] Command: git {' '.join(git_command)}")
        print(f"[DEBUG] Ignore patterns: {ignore_patterns}")
    
    # Check if we need to stash RIGHT NOW
    needs_stash = has_ignored_changes(repo_path, ignore_patterns)
    
    if verbose:
        print(f"[DEBUG] Ignorable changes detected: {needs_stash}")
    
    if dry_run:
        print(f"[DRY-RUN] Would check for ignorable changes: {needs_stash}")
        if needs_stash:
            print(f"[DRY-RUN] Would stash files matching: {ignore_patterns}")
        print(f"[DRY-RUN] Would run: git {' '.join(git_command)}")
        if needs_stash:
            print(f"[DRY-RUN] Would restore stash after command")
        
        if json_output:
            return {
                "success": True,
                "command": git_command,
                "stdout": "[DRY-RUN]",
                "stderr": "",
                "stashed": needs_stash,
                "restored": needs_stash,
                "dry_run": True
            }
        else:
            return subprocess.CompletedProcess(["git"] + git_command, 0, "[DRY-RUN]", "")
    
    stash_ref = None
    stash_message = f"Auto-stash: {description}"
    
    if needs_stash:
        print(f"\n🔒 Stashing ignorable changes before {description}...")
        
        # Create pathspecs for git stash push
        # This only stashes matching files, not everything
        stash_cmd = ["stash", "push", "-m", stash_message]
        
        # Add -- separator and patterns
        stash_cmd.append("--")
        for pattern in ignore_patterns:
            stash_cmd.append(pattern)
        
        stash_result = run_git(stash_cmd, cwd=repo_path, check=False)
        
        if stash_result.returncode == 0:
            print("✓ Stashed")
            # Get the exact stash reference using git stash list
            list_result = run_git(["stash", "list", "--format=%gd:%gs"], cwd=repo_path, check=False)
            for line in list_result.stdout.splitlines():
                if stash_message in line:
                    stash_ref = line.split(':', 1)[0]  # e.g., stash@{0}
                    if verbose:
                        print(f"[DEBUG] Stash reference: {stash_ref}")
                    break
        else:
            if verbose:
                print(f"[DEBUG] Stash failed: {stash_result.stderr}")
            # Continue anyway - stash failure shouldn't block operation
    
    # Immediately run the git command
    if verbose:
        print(f"[DEBUG] Running git command...")
    
    result = run_git(git_command, cwd=repo_path, check=False)
    
    if verbose:
        print(f"[DEBUG] Command exit code: {result.returncode}")
        if result.returncode != 0:
            print(f"[DEBUG] Command stderr: {result.stderr}")
    
    # Restore if we stashed - but only if command succeeded or is safe to restore
    safe_to_restore = result.returncode == 0 or result.returncode in [1]  # 1 = merge conflicts, safe to restore
    
    if stash_ref and safe_to_restore:
        print(f"↩️  Restoring changes after {description}...")
        
        # Pop the specific stash by reference
        pop_result = run_git(["stash", "pop", stash_ref], cwd=repo_path, check=False)
        
        if pop_result.returncode == 0:
            print("✓ Restored")
        else:
            # Stash pop failed - likely conflicts
            if "conflict" in pop_result.stderr.lower():
                print("⚠️  Stash had conflicts. Keeping stash for manual resolution.")
                print(f"   Run 'git stash pop {stash_ref}' manually when ready.")
            else:
                if verbose:
                    print(f"[DEBUG] Stash pop issues: {pop_result.stderr}")
    elif stash_ref and not safe_to_restore:
        if verbose:
            print(f"[DEBUG] Git command failed badly (code {result.returncode}), keeping stash for safety")
        print(f"⚠️  Command failed, stash kept. Restore with: git stash pop {stash_ref}")
    
    if json_output:
        return {
            "success": result.returncode == 0,
            "command": git_command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stashed": bool(stash_ref),
            "restored": bool(stash_ref and safe_to_restore),
            "stash_ref": stash_ref,
            "returncode": result.returncode
        }
    
    return result


def stash_ignored_changes(repo_path: Path, description: str, patterns: Optional[List[str]] = None) -> bool:
    """
    Manually stash ignorable changes.
    
    Args:
        repo_path: Path to git repository
        description: Description for stash message
        patterns: File patterns to stash (uses config if None)
    
    Returns:
        True if changes were stashed, False otherwise
    """
    if patterns is None:
        patterns = get_ignore_patterns(repo_path)
    
    if not has_ignored_changes(repo_path, patterns):
        return False
    
    print(f"\n⚠️  Ignorable file changes detected: {', '.join(patterns)}")
    print("These will be stashed.")
    
    stash_cmd = ["stash", "push", "-m", f"Manual stash: {description}", "--"]
    stash_cmd.extend(patterns)
    
    result = run_git(stash_cmd, cwd=repo_path, check=False)
    
    if result.returncode == 0:
        print("✓ Changes stashed")
        return True
    else:
        print(f"⚠️  Stash failed: {result.stderr}")
        return False


def restore_latest_stash(repo_path: Path, stash_name: Optional[str] = None) -> bool:
    """
    Restore the most recent stash (or a specific stash).
    
    Args:
        repo_path: Path to git repository
        stash_name: Optional stash message to look for
    
    Returns:
        True if stash was restored, False otherwise
    """
    # Check if stash exists
    stash_list = run_git(["stash", "list"], cwd=repo_path, check=False)
    
    if not stash_list.stdout.strip():
        print("No stashes to restore")
        return False
    
    if stash_name and stash_name not in stash_list.stdout:
        print(f"Stash '{stash_name}' not found")
        return False
    
    print("\n↩️  Restoring stashed changes...")
    
    result = run_git(["stash", "pop"], cwd=repo_path, check=False)
    
    if result.returncode == 0:
        print("✓ Stash restored")
        return True
    else:
        if "conflict" in result.stderr.lower():
            print("⚠️  Stash had conflicts. Resolve conflicts and drop stash manually.")
        else:
            print(f"⚠️  Stash restore failed: {result.stderr}")
        return False


def list_stashes(repo_path: Path):
    """Display all stashes with details."""
    result = run_git(["stash", "list"], cwd=repo_path, check=False)
    
    if not result.stdout.strip():
        print("\nNo stashes found")
        return
    
    print("\n📦 Stashes:")
    for line in result.stdout.strip().split('\n'):
        print(f"  {line}")
    
    print(f"\nTo restore: git stash pop")
    print(f"To view:    git stash show -p stash@{{0}}")
    print(f"To drop:    git stash drop stash@{{0}}")


def add_ignore_pattern(pattern: str, project_path: Optional[Path] = None):
    """
    Add a file pattern to ignore list for git operations.
    
    Args:
        pattern: File pattern (e.g., "*.po", "docs/generated/*")
        project_path: Project path (uses cwd if None)
    """
    from gitship.config import load_config, save_config
    
    if project_path is None:
        project_path = Path.cwd()
    
    config = load_config()
    project_key = str(project_path.resolve())
    
    # Initialize project_ignore_patterns if not present
    if 'project_ignore_patterns' not in config:
        config['project_ignore_patterns'] = {}
    
    # Get or create ignore list for this project
    if project_key not in config['project_ignore_patterns']:
        config['project_ignore_patterns'][project_key] = get_default_ignore_patterns()
    
    patterns = config['project_ignore_patterns'][project_key]
    
    if pattern not in patterns:
        patterns.append(pattern)
        config['project_ignore_patterns'][project_key] = patterns
        save_config(config)
        print(f"✓ Added '{pattern}' to ignore patterns for this project")
    else:
        print(f"Pattern '{pattern}' already in ignore list")


def remove_ignore_pattern(pattern: str, project_path: Optional[Path] = None):
    """
    Remove a file pattern from ignore list.
    
    Args:
        pattern: File pattern to remove
        project_path: Project path (uses cwd if None)
    """
    from gitship.config import load_config, save_config
    
    if project_path is None:
        project_path = Path.cwd()
    
    config = load_config()
    project_key = str(project_path.resolve())
    
    project_patterns = config.get('project_ignore_patterns', {})
    
    if project_key in project_patterns:
        if pattern in project_patterns[project_key]:
            project_patterns[project_key].remove(pattern)
            config['project_ignore_patterns'] = project_patterns
            save_config(config)
            print(f"✓ Removed '{pattern}' from ignore patterns")
        else:
            print(f"Pattern '{pattern}' not in ignore list")
    else:
        print("No ignore patterns configured for this project")


def list_ignore_patterns(project_path: Optional[Path] = None):
    """Display ignore patterns for the current project."""
    if project_path is None:
        project_path = Path.cwd()
    
    patterns = get_ignore_patterns(project_path)
    project_name = project_path.name
    
    print(f"\n📋 Ignore patterns for '{project_name}':")
    if patterns:
        for pattern in patterns:
            print(f"  • {pattern}")
    else:
        print("  (none - all changes will be included)")
    print()