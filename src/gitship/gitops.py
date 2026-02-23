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


def _get_matched_files(repo_path: Path, patterns: List[str]) -> List[str]:
    """Return the list of dirty files that match the given ignore patterns."""
    result = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return []
    matched = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            filepath = parts[1].strip()
            for pattern in patterns:
                if fnmatch.fnmatch(filepath, pattern) or fnmatch.fnmatch(Path(filepath).name, pattern):
                    matched.append(filepath)
                    break
    return matched


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
        
        # Build a richer stash message that includes the actual filenames,
        # so the stash history is searchable and self-documenting.
        matched_files = _get_matched_files(repo_path, ignore_patterns)
        if matched_files:
            file_summary = ", ".join(matched_files[:5])
            if len(matched_files) > 5:
                file_summary += f" (+{len(matched_files) - 5} more)"
            stash_message = f"Auto-stash [{description}]: {file_summary}"
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


def capture_file_snapshot(repo_path: Path, filepath: str) -> Optional[Dict[str, Any]]:
    """
    Capture the exact current diff of a file vs HEAD as a frozen snapshot.
    
    This records what changed RIGHT NOW, so even if the AI keeps modifying
    the file while the user reviews, we can restore exactly this state at
    commit time.
    
    Args:
        repo_path: Path to git repository
        filepath: Relative path to file within repo
        
    Returns:
        Dict with 'filepath', 'patch' (unified diff text), 'head_content',
        'snapshot_content', and 'lang_code' (extracted from locale path).
        Returns None if file has no changes vs HEAD.
    """
    abs_path = repo_path / filepath
    
    # Get HEAD content
    head_result = run_git(["show", f"HEAD:{filepath}"], cwd=repo_path, check=False)
    head_content = head_result.stdout if head_result.returncode == 0 else ""
    
    # Get current working tree content
    if not abs_path.exists():
        return None
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            current_content = f.read()
    except Exception:
        return None
    
    if current_content == head_content:
        return None  # No changes
    
    # Build unified diff as our frozen patch
    import difflib
    head_lines = head_content.splitlines(keepends=True)
    current_lines = current_content.splitlines(keepends=True)
    patch_lines = list(difflib.unified_diff(
        head_lines, current_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        lineterm=''
    ))
    patch_text = '\n'.join(patch_lines)
    
    # Extract language code from locale path, e.g.:
    #   src/omnipkg/locale/ar_eg/LC_MESSAGES/omnipkg.po  ->  ar_eg
    #   locale/fr/LC_MESSAGES/app.po                      ->  fr
    lang_code = None
    path_parts = Path(filepath).parts
    for i, part in enumerate(path_parts):
        if part == 'locale' and i + 1 < len(path_parts):
            lang_code = path_parts[i + 1]
            break
    
    return {
        'filepath': filepath,
        'patch': patch_text,
        'head_content': head_content,
        'snapshot_content': current_content,
        'lang_code': lang_code,
    }


def capture_translation_snapshots(repo_path: Path, trans_file_list: List[Dict]) -> List[Dict[str, Any]]:
    """
    Capture frozen snapshots for all translation files right now.
    
    Call this when the user says "yes I want to include these translations".
    Returns a list of snapshot dicts (from capture_file_snapshot) for files
    that actually have changes.
    """
    snapshots = []
    for file_info in trans_file_list:
        filepath = file_info.get('path') or file_info.get('filepath')
        if not filepath:
            continue
        snap = capture_file_snapshot(repo_path, filepath)
        if snap:
            snapshots.append(snap)
    return snapshots


def atomic_commit_with_snapshot(
    repo_path: Path,
    snapshots: List[Dict[str, Any]],
    commit_message: str,
    ignore_patterns: Optional[List[str]] = None,
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """
    Atomically commit a frozen snapshot of translation files alongside other changes.
    
    The problem this solves: an AI process keeps writing to .po files continuously.
    By the time the user finishes reviewing and writing a commit message, the files
    have changed further. We want to commit exactly what the user reviewed, not the
    latest AI output.
    
    Algorithm:
      1. Stash EVERYTHING matching ignore_patterns (the latest AI output)
      2. For each snapshot: write snapshot_content directly to the file
         (this is exactly what was showing when user reviewed)  
      3. git add those specific files
      4. git commit (picks up the snapshot content + any other staged/unstaged changes)
      5. git stash pop (restore the AI's latest work on top)
    
    Args:
        repo_path: Path to git repository
        snapshots: List of snapshot dicts from capture_file_snapshot()
        commit_message: Full commit message string
        ignore_patterns: Patterns to stash (uses project config if None)
        verbose: Print debug info
        
    Returns:
        CompletedProcess from the commit command
    """
    if ignore_patterns is None:
        ignore_patterns = get_ignore_patterns(repo_path)
    
    if not snapshots:
        # Nothing to snapshot-commit — fall through to plain atomic operation
        return atomic_git_operation(
            repo_path=repo_path,
            git_command=["commit", "-a", "-m", commit_message],
            description="commit",
            ignore_patterns=ignore_patterns,
            verbose=verbose,
        )
    
    if verbose:
        print(f"[DEBUG] atomic_commit_with_snapshot: {len(snapshots)} translation file(s)")
        for s in snapshots:
            print(f"[DEBUG]   {s['filepath']} (lang: {s.get('lang_code', '?')})")
    
    stash_ref = None
    stash_message = "Auto-stash: pre-snapshot-commit"
    
    # ── Step 1: Stash current (AI-latest) versions of ignored files ──────────
    needs_stash = has_ignored_changes(repo_path, ignore_patterns)
    if verbose:
        print(f"[DEBUG] needs_stash = {needs_stash}")
    
    if needs_stash:
        print("\n🔒 Stashing AI-modified translations to restore reviewed state...")
        stash_cmd = ["stash", "push", "-m", stash_message, "--"]
        stash_cmd.extend(ignore_patterns)
        stash_result = run_git(stash_cmd, cwd=repo_path, check=False)
        
        if stash_result.returncode == 0:
            print("✓ Stashed")
            list_result = run_git(
                ["stash", "list", "--format=%gd:%gs"], cwd=repo_path, check=False
            )
            for line in list_result.stdout.splitlines():
                if stash_message in line:
                    stash_ref = line.split(':', 1)[0]
                    break
            if verbose:
                print(f"[DEBUG] stash_ref = {stash_ref}")
        else:
            if verbose:
                print(f"[DEBUG] Stash failed: {stash_result.stderr}")
            print("⚠  Could not stash, proceeding anyway (snapshot will still be applied)")
    
    # ── Step 2: Write the frozen snapshot content to each file ───────────────
    print("\n📌 Restoring reviewed snapshot state...")
    restored_files = []
    failed_files = []
    
    for snap in snapshots:
        filepath = snap['filepath']
        abs_path = repo_path / filepath
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(snap['snapshot_content'])
            restored_files.append(filepath)
            if verbose:
                print(f"[DEBUG]   Wrote snapshot → {filepath}")
        except Exception as e:
            failed_files.append((filepath, str(e)))
            print(f"  ✗ Failed to restore {filepath}: {e}")
    
    if failed_files and not restored_files:
        # Everything failed — pop the stash and abort
        if stash_ref:
            run_git(["stash", "pop", stash_ref], cwd=repo_path, check=False)
        result = subprocess.CompletedProcess(["git", "commit"], 1, "", "Failed to write any snapshot content")
        return result
    
    for fp in restored_files:
        print(f"  ✓ {fp}")
    
    # ── Step 3: Stage the snapshot files ─────────────────────────────────────
    print("\n📎 Staging snapshot files...")
    for filepath in restored_files:
        stage_result = run_git(["add", filepath], cwd=repo_path, check=False)
        if stage_result.returncode == 0:
            if verbose:
                print(f"[DEBUG]   Staged {filepath}")
        else:
            print(f"  ⚠  Could not stage {filepath}: {stage_result.stderr}")
    
    # ── Step 4: Commit ────────────────────────────────────────────────────────
    print("\n💾 Committing...")
    commit_result = run_git(["commit", "-m", commit_message], cwd=repo_path, check=False)
    
    if commit_result.returncode == 0:
        print("✓ Committed")
        if verbose:
            print(commit_result.stdout)
    else:
        print(f"✗ Commit failed: {commit_result.stderr}")
    
    # ── Step 5: Pop the stash to restore AI's latest work ────────────────────
    if stash_ref:
        print("\n↩️  Restoring AI's latest translation work...")
        pop_result = run_git(["stash", "pop", stash_ref], cwd=repo_path, check=False)
        if pop_result.returncode == 0:
            print("✓ Restored")
        elif "conflict" in (pop_result.stderr + pop_result.stdout).lower():
            # Binary files (e.g. .mo) can never be 3-way merged by git — they will
            # always show as conflicted even though "theirs" (the stash = AI's latest)
            # is exactly what we want.  Detect all UU files, resolve binaries with
            # --theirs automatically, and leave text conflicts for the user.
            print("  Conflicts detected — checking for binary files to auto-resolve...")
            
            status_result = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
            conflicted = []
            for line in status_result.stdout.splitlines():
                if line.startswith("UU ") or line.startswith("AA "):
                    conflicted.append(line[3:].strip())
            
            binary_resolved = []
            text_conflicted = []
            for filepath in conflicted:
                # Check if file is binary by asking git
                check = run_git(
                    ["diff", "--numstat", "HEAD", "--", filepath],
                    cwd=repo_path, check=False
                )
                # git diff --numstat outputs "-\t-\tfilename" for binary files
                if check.stdout.strip().startswith("-\t-\t"):
                    # Binary — just take the stash (AI's latest) version
                    run_git(["checkout", "--theirs", "--", filepath], cwd=repo_path, check=False)
                    run_git(["add", "--", filepath], cwd=repo_path, check=False)
                    binary_resolved.append(filepath)
                else:
                    text_conflicted.append(filepath)
            
            if binary_resolved:
                print(f"✓ Auto-resolved {len(binary_resolved)} binary file(s) (took AI's latest version)")
                if verbose:
                    for f in binary_resolved:
                        print(f"  [DEBUG] binary resolved: {f}")
            
            if text_conflicted:
                print(f"⚠  {len(text_conflicted)} text file(s) still have conflicts — resolve manually:")
                for f in text_conflicted:
                    print(f"   git checkout --theirs -- {f} && git add {f}")
                print(f"   Then: git stash drop {stash_ref}")
            else:
                # All conflicts resolved — drop the now-empty stash
                run_git(["stash", "drop", stash_ref], cwd=repo_path, check=False)
                print("✓ Stash cleaned up — working tree fully restored")
        else:
            if verbose:
                print(f"[DEBUG] Pop stderr: {pop_result.stderr}")
            print(f"⚠  Could not pop stash automatically. Run: git stash pop {stash_ref}")
    
    return commit_result


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
    
    matched_files = _get_matched_files(repo_path, patterns)
    if matched_files:
        file_summary = ", ".join(matched_files[:5])
        if len(matched_files) > 5:
            file_summary += f" (+{len(matched_files) - 5} more)"
        stash_label = f"Manual stash [{description}]: {file_summary}"
    else:
        stash_label = f"Manual stash: {description}"
    
    stash_cmd = ["stash", "push", "-m", stash_label, "--"]
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