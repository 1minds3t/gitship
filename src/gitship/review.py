#!/usr/bin/env python3
import re
"""
reviewgit - Comprehensive commit review between tags/commits with export.

Features:
- Show git diff --stat between two references (tags, commits, branches)
- Display all commit messages with full descriptions
- Export full diffs to structured text files
- Configurable export path with historical metadata
- Interactive prompts for user preferences
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple
import json

from .check import GitCraftLogger, run_git_command, is_git_repo, get_branch_name

def get_all_tags(repo_path: Path) -> List[str]:
    """Get all tags sorted by date (newest first)."""
    result = run_git_command(["tag", "--sort=-creatordate"], cwd=repo_path)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split('\n')
    return []

def get_last_tag(repo_path: Path) -> Optional[str]:
    """Get the most recent tag in the repository."""
    result = run_git_command(["describe", "--tags", "--abbrev=0"], cwd=repo_path)
    
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    
    # Try to get any tag
    result = run_git_command(["tag", "--sort=-creatordate"], cwd=repo_path)
    if result.returncode == 0 and result.stdout.strip():
        tags = result.stdout.strip().split('\n')
        return tags[0] if tags else None
    
    return None


def get_commits_between(repo_path: Path, from_ref: str, to_ref: str) -> List[dict]:
    """Get list of commits between two references."""
    result = run_git_command([
        "log",
        f"{from_ref}..{to_ref}",
        "--pretty=format:%H|%h|%an|%ae|%ad|%s",
        "--date=iso"
    ], cwd=repo_path)
    
    if result.returncode != 0:
        return []
    
    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|', 5)
        if len(parts) == 6:
            commits.append({
                'hash': parts[0],
                'short_hash': parts[1],
                'author': parts[2],
                'email': parts[3],
                'date': parts[4],
                'subject': parts[5]
            })
    
    return commits


def get_commit_body(repo_path: Path, commit_hash: str) -> str:
    """Get the full commit message body."""
    result = run_git_command([
        "log", "-1", "--pretty=format:%B", commit_hash
    ], cwd=repo_path)
    
    return result.stdout.strip() if result.returncode == 0 else ""


def get_commit_stats(repo_path: Path, commit_hash: str) -> str:
    """Get diff stats for a specific commit."""
    result = run_git_command([
        "show", "--stat", "--pretty=format:", commit_hash
    ], cwd=repo_path)
    
    return result.stdout.strip() if result.returncode == 0 else ""


def get_diff_stat(repo_path: Path, from_ref: str, to_ref: str) -> str:
    """Get diff statistics between two references."""
    result = run_git_command([
        "diff", "--stat", f"{from_ref}..{to_ref}"
    ], cwd=repo_path)
    
    return result.stdout.strip() if result.returncode == 0 else ""


def get_full_diff(repo_path: Path, from_ref: str, to_ref: str) -> str:
    """Get full diff between two references."""
    result = run_git_command([
        "diff", f"{from_ref}..{to_ref}"
    ], cwd=repo_path)
    
    return result.stdout.strip() if result.returncode == 0 else ""


def create_export_filename(repo_name: str, from_ref: str, to_ref: str) -> str:
    """Create a structured filename for the export."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_from = from_ref.replace('/', '_').replace(' ', '_')
    safe_to = to_ref.replace('/', '_').replace(' ', '_')
    
    return f"{repo_name}_diff_{safe_from}_to_{safe_to}_{timestamp}.txt"

def is_merge_commit(repo_path: Path, commit_hash: str) -> Tuple[bool, List[str]]:
    """
    Check if a commit is a merge commit and return its parents.
    Returns (is_merge, [parent_hashes])
    """
    result = run_git_command(["show", "--no-patch", "--format=%P", commit_hash], cwd=repo_path)
    
    if result.returncode != 0:
        return False, []
    
    parents = result.stdout.strip().split()
    return len(parents) >= 2, parents

def get_merged_commits(repo_path: Path, parent1: str, parent2: str) -> List[dict]:
    """
    Get the list of commits that were brought in by the merge.
    Essentially: git log parent1..parent2
    """
    # parent1 is usually the base (main), parent2 is the branch being merged (development)
    return get_commits_between(repo_path, parent1, parent2)

def export_diff_to_file(
    export_path: Path,
    repo_name: str,
    from_ref: str,
    to_ref: str,
    diff_stat: str,
    commits: List[dict],
    commit_details: dict,
    full_diff: str,
    logger: GitCraftLogger
) -> Path:
    """Export comprehensive diff information to a structured text file."""
    
    # Create export directory if it doesn't exist
    export_path.mkdir(parents=True, exist_ok=True)
    
    # Create filename
    filename = create_export_filename(repo_name, from_ref, to_ref)
    filepath = export_path / filename
    
    # Build the export content
    content_parts = []
    
    # Header
    content_parts.append("=" * 80)
    content_parts.append(f"GIT DIFF EXPORT: {repo_name}")
    content_parts.append("=" * 80)
    content_parts.append(f"From: {from_ref}")
    content_parts.append(f"To: {to_ref}")
    content_parts.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    content_parts.append(f"Total Commits: {len(commits)}")
    content_parts.append("=" * 80)
    content_parts.append("")
    
    # Summary Statistics
    content_parts.append("=" * 80)
    content_parts.append("SUMMARY STATISTICS")
    content_parts.append("=" * 80)
    content_parts.append(diff_stat)
    content_parts.append("")
    content_parts.append("")
    
    # Commit List with Full Messages
    content_parts.append("=" * 80)
    content_parts.append("COMMIT HISTORY")
    content_parts.append("=" * 80)
    content_parts.append("")
    
    for i, commit in enumerate(commits, 1):
        content_parts.append(f"[{i}] {commit['short_hash']} - {commit['date']}")
        content_parts.append(f"Author: {commit['author']} <{commit['email']}>")
        content_parts.append(f"Subject: {commit['subject']}")
        content_parts.append("")
        
        # Full message body
        body = commit_details.get(commit['hash'], '')
        if body:
            content_parts.append("Message:")
            content_parts.append(body)
            content_parts.append("")
        
        # Stats for this commit
        stats = commit_details.get(f"{commit['hash']}_stats", '')
        if stats:
            content_parts.append("Files changed:")
            content_parts.append(stats)
            content_parts.append("")
        
        content_parts.append("-" * 80)
        content_parts.append("")
    
    # Full Diff
    content_parts.append("")
    content_parts.append("=" * 80)
    content_parts.append("FULL DIFF")
    content_parts.append("=" * 80)
    content_parts.append("")
    content_parts.append(full_diff)
    
    # Write to file
    try:
        filepath.write_text('\n'.join(content_parts))
        logger.info(f"Exported diff to: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to export diff: {e}")
        raise

def is_generic_message(subject: str) -> bool:
    """Detect if a commit message is uninformative/generic."""
    patterns = [
        r"^Update\s+\S+",          # Update file.ext
        r"^Delete\s+\S+",          # Delete file.ext
        r"^Create\s+\S+",          # Create file.ext
        r"^Sync\s+.*",             # Sync ...
        r"^fix\s*$",               # fix
        r"^typo\s*$",              # typo
        r"^wip\s*$",               # wip
        r"^bump\s+version",        # bump version
    ]
    return any(re.match(p, subject, re.IGNORECASE) for p in patterns)

def get_smart_context(repo_path: Path, commit_hash: str, is_generic: bool) -> Tuple[str, str]:
    """
    Get context for a commit.
    Returns: (shortstat_string, diff_preview_string)
    """
    # 1. Get Short Stat
    stat_res = run_git_command(
        ["show", "--shortstat", "--format=", commit_hash], 
        cwd=repo_path
    )
    stat = stat_res.stdout.strip()
    
    preview = ""
    if is_generic:
        # 2. Get the diff with colors
        diff_res = run_git_command(
            ["show", "--patch", "--format=", "--color=always", commit_hash], 
            cwd=repo_path
        )
        
        if diff_res.returncode == 0:
            lines = diff_res.stdout.splitlines()
            cleaned_lines = []
            
            # Helper to strip ANSI codes for checking line content
            ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
            
            for line in lines:
                # Strip color to check the text content
                plain = ansi_escape.sub('', line)
                
                # Skip Git metadata headers
                if plain.startswith((
                    'diff --git', 
                    'index ', 
                    '--- a/', 
                    '+++ b/', 
                    '--- /dev/null', 
                    '+++ /dev/null',
                    'new file mode', 
                    'deleted file mode'
                )):
                    continue
                
                cleaned_lines.append(line)
            
            # Grab first 15 lines of ACTUAL content
            preview_lines = cleaned_lines[:15]
            if len(cleaned_lines) > 15:
                preview_lines.append(f"\x1b[33m... ({len(cleaned_lines) - 15} more lines truncated) ...\x1b[0m")
            
            preview = "\n".join(preview_lines)

    return stat, preview

def display_commits_summary(commits: List[dict], commit_details: dict, repo_path: Path = None):
    """Display commit summary with messages, auto-expand merges, and smart context."""
    print("\n" + "=" * 80)
    print("COMMIT MESSAGES")
    print("=" * 80)
    print()
    
    for i, commit in enumerate(commits, 1):
        print(f"[{i}] {commit['short_hash']} - {commit['date']}")
        print(f"Author: {commit['author']} <{commit['email']}>")
        print(f"Subject: {commit['subject']}")
        
        # --- NEW: Smart Context (Stat + Diff Peek) ---
        if repo_path:
            is_generic = is_generic_message(commit['subject'])
            
            # Check for merge first (priority)
            is_merge, parents = is_merge_commit(repo_path, commit['hash'])
            
            if is_merge:
                print(f"\n  [MERGE DETECTED] This merged {len(parents)} branches.")
                merged_sub_commits = get_merged_commits(repo_path, parents[0], parents[1])
                if merged_sub_commits:
                    print(f"  Included {len(merged_sub_commits)} commits:")
                    for sub in merged_sub_commits:
                        print(f"    * {sub['short_hash']} - {sub['subject']}")
            
            else:
                # Get stats and context for non-merges
                stat, preview = get_smart_context(repo_path, commit['hash'], is_generic)
                
                if stat:
                    print(f"  Stats: {stat}")
                
                if is_generic and preview:
                    print("\n  [AUTO-PREVIEW] Generic message detected. Here is what changed:")
                    print("-" * 40)
                    # Indent the diff output
                    for line in preview.splitlines():
                        print(f"  {line}")
                    print("-" * 40)

        # --- End New Context ---

        body = commit_details.get(commit['hash'], '')
        if body and body != commit['subject']:
            print("\nDescription:")
            for line in body.split('\n'):
                if line.strip() and line.strip() != commit['subject']:
                    print(f"  {line}")
        
        print()
        print("-" * 80)
        print()

def display_commit_stats(commits: List[dict], commit_details: dict):
    """Display individual commit stats."""
    print("\n" + "=" * 80)
    print("INDIVIDUAL COMMIT STATISTICS")
    print("=" * 80)
    print()
    
    for i, commit in enumerate(commits, 1):
        print(f"[{i}] {commit['short_hash']} - {commit['subject']}")
        stats = commit_details.get(f"{commit['hash']}_stats", '')
        if stats:
            print(stats)
        print()


def main_with_args(
    repo_path: Path,
    from_ref: Optional[str] = None,
    to_ref: str = 'HEAD',
    export: bool = False,
    export_path: Optional[str] = None,
    stat_only: bool = False
):
    """Main function for reviewgit with arguments."""
    logger = GitCraftLogger("reviewgit")
    
    # Validate repository
    if not is_git_repo(repo_path):
        logger.error(f"Not in a git repository: {repo_path}")
        sys.exit(1)
    
    repo_name = repo_path.name
    branch = get_branch_name(repo_path)
    
    # Determine from_ref
    if from_ref is None:
        from_ref = get_last_tag(repo_path)
        if from_ref is None:
            logger.error("No tags found in repository. Please specify --from reference.")
            print("Error: No tags found. Use --from to specify a starting reference.")
            sys.exit(1)
        logger.info(f"Using last tag as starting reference: {from_ref}")
    
    logger.info(f"Reviewing changes from {from_ref} to {to_ref} in {repo_path}")
    
    print("\n" + "=" * 80)
    print(f"GITSHIP REVIEW: {repo_name} (branch: {branch})")
    print("=" * 80)
    print(f"From: {from_ref}")
    print(f"To: {to_ref}")
    print("=" * 80)
    
    # Get diff stat
    print("\n" + "=" * 80)
    print("DIFF STATISTICS")
    print("=" * 80)
    diff_stat = get_diff_stat(repo_path, from_ref, to_ref)
    print(diff_stat)
    print()
    
    if stat_only:
        return
    
    # Get commits
    commits = get_commits_between(repo_path, from_ref, to_ref)
    
    if not commits:
        print("No commits found between these references.")
        return
    
    print(f"\nTotal commits: {len(commits)}")
    
    # Ask user what they want to see
    print("\nWhat would you like to review?")
    print("  1. Show all commit messages (with descriptions)")
    print("  2. Show individual commit statistics")
    print("  3. Show both messages and statistics")
    print("  4. Export everything to file")
    print("  0. Skip and exit")
    
    choice = input("\nEnter your choice (0-4): ").strip()
    
    # Collect commit details if needed
    commit_details = {}
    if choice in ('1', '2', '3', '4'):
        print("\nCollecting commit details...")
        for commit in commits:
            commit_details[commit['hash']] = get_commit_body(repo_path, commit['hash'])
            if choice in ('2', '3', '4'):
                commit_details[f"{commit['hash']}_stats"] = get_commit_stats(repo_path, commit['hash'])
    
    # Display based on choice
    if choice == '1':
        # PASS repo_path HERE so the expansion works
        display_commits_summary(commits, commit_details, repo_path=repo_path)
    
    elif choice == '2':
        display_commit_stats(commits, commit_details)
    
    elif choice == '3':
        # PASS repo_path HERE too
        display_commits_summary(commits, commit_details, repo_path=repo_path)
        display_commit_stats(commits, commit_details)
    
    elif choice == '4' or export:
        # Determine export path
        if export_path:
            exp_path = Path(export_path)
        else:
            from .config import get_default_export_path
            exp_path = get_default_export_path()
        
        print(f"\nExport path: {exp_path}")
        
        # Get full diff
        print("Collecting full diff...")
        full_diff = get_full_diff(repo_path, from_ref, to_ref)
        
        # Export
        exported_file = export_diff_to_file(
            exp_path,
            repo_name,
            from_ref,
            to_ref,
            diff_stat,
            commits,
            commit_details,
            full_diff,
            logger
        )
        
        print(f"\n✓ Successfully exported to: {exported_file}")
        print(f"  File size: {exported_file.stat().st_size / 1024:.2f} KB")
    
    elif choice == '0':
        print("Exiting...")
        return
    
    else:
        print(f"Invalid choice: {choice}")


def main_with_repo(repo_path: Path):
    """Interactive mode with prompts for range selection."""
    tags = get_all_tags(repo_path)
    
    # Try to get PyPI latest version for better comparison
    pypi_tag = None
    try:
        # Import here to avoid circular dependency
        import sys
        import requests
        
        # Try to read package name from pyproject.toml
        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            import re
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                pkg_name = match.group(1)
                resp = requests.get(f"https://pypi.org/pypi/{pkg_name}/json", timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    latest = data.get('info', {}).get('version')
                    if latest:
                        pypi_tag = f"v{latest}"
    except:
        pass
    
    # Use PyPI tag if available, otherwise use git tag
    current_ver = pypi_tag if pypi_tag else (tags[0] if tags else "None")
    pypi_note = " (from PyPI)" if pypi_tag else ""
    
    print("\n" + "=" * 60)
    print("REVIEWGIT - Select Range")
    print("=" * 60)
    print(f"Latest Tag: {current_ver}{pypi_note}")
    print("\nModes:")
    print("  1. Latest Tag -> HEAD   (Review current work)")
    print("  2. Select Tags          (Compare specific releases)")
    print("  3. Manual Input         (Commits, branches, etc)")
    
    choice = input("\nChoice [1]: ").strip()
    
    from_ref = None
    to_ref = "HEAD"
    
    if choice == '2':
        if not tags:
            print("❌ No tags found in repository.")
            return
        
        print("\nAvailable Tags:")
        limit = min(len(tags), 15)
        for i in range(limit):
            print(f"  {i+1}. {tags[i]}")
        if len(tags) > 15:
            print(f"  ... ({len(tags)-15} more)")
            
        print("\nTip: Enter number (e.g. 2) or tag name")
        
        # Select START (Older)
        # Default to 2nd tag (Previous Release) if available
        def_idx = 2 if len(tags) >= 2 else 1
        def_tag = tags[def_idx-1]
        
        raw_start = input(f"Start Point (Older) [{def_tag}]: ").strip()
        
        if not raw_start:
            from_ref = def_tag
        elif raw_start.isdigit():
            idx = int(raw_start) - 1
            if 0 <= idx < len(tags):
                from_ref = tags[idx]
            else:
                print("Invalid index.")
                return
        else:
            from_ref = raw_start

        # Select END (Newer)
        raw_end = input(f"End Point (Newer) [HEAD]: ").strip()
        if not raw_end:
            to_ref = "HEAD"
        elif raw_end.isdigit():
            idx = int(raw_end) - 1
            if 0 <= idx < len(tags):
                to_ref = tags[idx]
            else:
                print("Invalid index.")
                return
        else:
            to_ref = raw_end
            
    elif choice == '3':
        from_ref = input("Start Reference (SHA/Ref): ").strip()
        if not from_ref: return
        to_ref = input("End Reference [HEAD]: ").strip() or "HEAD"

    # Execute
    main_with_args(repo_path=repo_path, from_ref=from_ref, to_ref=to_ref)


def main():
    """Main entry point for reviewgit standalone."""
    repo_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    main_with_repo(repo_path)


if __name__ == "__main__":
    main()