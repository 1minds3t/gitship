#!/usr/bin/env python3
"""
Shared changelog generation utilities for gitship.

This module provides smart changelog generation that can be used by both
the commit and release commands. It analyzes actual file changes instead of
just parsing commit messages.
"""

import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import re


# Marker to identify commits made by gitship commit tool
GITSHIP_COMMIT_MARKER = "[gitship-generated]"


def run_git(args: List[str], cwd: Path, check: bool = False) -> str:
    """Run a git command and return output."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except:
        return ""


def is_gitship_commit(commit_sha: str, repo_path: Path) -> bool:
    """Check if a commit was made by gitship commit tool."""
    message = run_git(["log", "-1", "--pretty=format:%B", commit_sha], repo_path)
    return GITSHIP_COMMIT_MARKER in message


def get_detailed_commits_since_tag(repo_path: Path, last_tag: str) -> List[Dict]:
    """
    Get detailed commit information since last tag.
    
    Returns list of commits with their messages and whether they were gitship-generated.
    Prioritizes merge commits and gitship-generated commits as they tend to be more detailed.
    """
    range_str = f"{last_tag}..HEAD" if last_tag else "HEAD"
    
    # Get commit SHAs and subjects
    log_output = run_git([
        "log", range_str, 
        "--pretty=format:%H|||%s|||%b",
        "--no-merges"  # We'll handle merges separately
    ], repo_path)
    
    commits = []
    seen_messages = set()
    
    for line in log_output.split('\n'):
        if not line.strip():
            continue
            
        parts = line.split('|||')
        if len(parts) < 2:
            continue
            
        sha = parts[0]
        subject = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ""
        
        # Skip noise
        if any(phrase in subject.lower() for phrase in [
            "merge", "auto-merge", "sync main", "sync development",
            "chore: release", "preparing release"
        ]):
            continue
        
        # Skip duplicates
        if subject in seen_messages:
            continue
        seen_messages.add(subject)
        
        # Check if gitship-generated
        is_gitship = GITSHIP_COMMIT_MARKER in body
        
        commits.append({
            'sha': sha,
            'subject': subject,
            'body': body,
            'is_gitship': is_gitship,
            'is_merge': False
        })
    
    # Also get merge commits (they often have detailed info)
    merge_log = run_git([
        "log", range_str,
        "--pretty=format:%H|||%s|||%b",
        "--merges"
    ], repo_path)
    
    for line in merge_log.split('\n'):
        if not line.strip():
            continue
            
        parts = line.split('|||')
        if len(parts) < 2:
            continue
            
        sha = parts[0]
        subject = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ""
        
        # Extract useful info from merge commits
        if "Merge pull request" in subject or "Merge branch" in subject:
            # Get the actual content from the merge body
            if body and not any(phrase in body.lower() for phrase in ["conflict", "auto-merge"]):
                commits.append({
                    'sha': sha,
                    'subject': body.split('\n')[0] if body else subject,
                    'body': body,
                    'is_gitship': False,
                    'is_merge': True
                })
    
    return commits


def analyze_uncommitted_changes(repo_path: Path) -> Optional[Dict]:
    """
    Check if there are uncommitted changes and return analysis.
    Returns None if no changes, otherwise returns change summary.
    """
    # Check for any changes
    status = run_git(["status", "--porcelain"], repo_path)
    if not status:
        return None
    
    # Quick analysis of change types
    lines = status.split('\n')
    staged = []
    unstaged = []
    untracked = []
    
    for line in lines:
        if not line:
            continue
        status_code = line[:2]
        filepath = line[3:].strip()
        
        if '?' in status_code:
            untracked.append(filepath)
        elif status_code[0] != ' ':
            staged.append(filepath)
        if status_code[1] != ' ' and status_code[1] != '?':
            unstaged.append(filepath)
    
    return {
        'staged': staged,
        'unstaged': unstaged,
        'untracked': untracked,
        'total': len(staged) + len(unstaged) + len(untracked)
    }


def extract_file_changes_from_gitship_commit(commit_body: str) -> List[str]:
    """
    Extract the detailed file change list from a gitship-generated commit.
    
    Gitship commits have a structured format with sections like:
    - New files:
    - Modified:
    - Renames:
    """
    lines = []
    in_section = False
    
    for line in commit_body.split('\n'):
        line = line.strip()
        
        # Detect section headers
        if line.endswith(':') and line in ['New files:', 'Modified:', 'Renames:']:
            in_section = True
            lines.append(f"**{line}**")
            continue
        
        # If we're in a section and line starts with bullet
        if in_section and line.startswith('•'):
            lines.append(f"- {line[1:].strip()}")
        elif in_section and not line:
            in_section = False
        elif not in_section and line and not line.startswith(GITSHIP_COMMIT_MARKER):
            # This might be a title line
            pass
    
    return lines


def generate_detailed_changelog(repo_path: Path, last_tag: str, new_version: str) -> Tuple[str, str]:
    """
    Generate a detailed changelog by analyzing commits and file changes.
    
    Returns (changelog_body, suggested_title)
    
    This function:
    1. Checks for uncommitted changes
    2. Looks for detailed gitship-generated commits
    3. Falls back to parsing commit messages
    4. Groups changes intelligently
    """
    commits = get_detailed_commits_since_tag(repo_path, last_tag)
    
    # Get file statistics
    range_str = f"{last_tag}..HEAD" if last_tag else "HEAD"
    stats = run_git(["diff", "--shortstat", range_str], repo_path)
    
    changelog_lines = []
    suggested_title = ""
    
    # Prioritize gitship-generated commits for details
    gitship_commits = [c for c in commits if c['is_gitship']]
    other_commits = [c for c in commits if not c['is_gitship']]
    
    # If we have detailed gitship commits, use their structured info
    if gitship_commits:
        # Use the most recent gitship commit's body as the primary source
        primary_commit = gitship_commits[0]
        
        # Extract the title from the commit subject
        if ':' in primary_commit['subject']:
            suggested_title = primary_commit['subject'].split(':', 1)[1].strip()
        else:
            suggested_title = primary_commit['subject']
        
        # Extract structured file changes
        file_changes = extract_file_changes_from_gitship_commit(primary_commit['body'])
        if file_changes:
            changelog_lines.extend(file_changes)
            changelog_lines.append("")
        
        # Add other gitship commits if they exist
        if len(gitship_commits) > 1:
            changelog_lines.append("**Additional Changes:**")
            for commit in gitship_commits[1:]:
                changelog_lines.append(f"- {commit['subject']}")
            changelog_lines.append("")
    
    # Add important non-gitship commits
    if other_commits:
        # Group by type
        features = []
        fixes = []
        updates = []
        other = []
        
        for commit in other_commits:
            subject = commit['subject']
            if subject.startswith(('feat:', 'feature:')):
                features.append(subject)
            elif subject.startswith('fix:'):
                fixes.append(subject)
            elif subject.startswith(('update:', 'Update ')):
                updates.append(subject)
            else:
                other.append(subject)
        
        if features:
            changelog_lines.append("**New Features:**")
            for f in features:
                changelog_lines.append(f"- {f}")
            changelog_lines.append("")
            if not suggested_title:
                suggested_title = features[0].split(':', 1)[1].strip() if ':' in features[0] else "New features"
        
        if fixes:
            changelog_lines.append("**Bug Fixes:**")
            for f in fixes:
                changelog_lines.append(f"- {f}")
            changelog_lines.append("")
            if not suggested_title:
                suggested_title = "Bug fixes and improvements"
        
        if updates:
            changelog_lines.append("**Updates:**")
            for u in updates[:10]:  # Limit to prevent spam
                changelog_lines.append(f"- {u}")
            if len(updates) > 10:
                changelog_lines.append(f"- ...and {len(updates) - 10} more updates")
            changelog_lines.append("")
        
        if other and not gitship_commits:
            # Only show "other" if we don't have detailed gitship commits
            changelog_lines.append("**Other Changes:**")
            for o in other[:5]:
                changelog_lines.append(f"- {o}")
            if len(other) > 5:
                changelog_lines.append(f"- ...and {len(other) - 5} more changes")
            changelog_lines.append("")
    
    # Add statistics
    if stats:
        changelog_lines.append(f"_{stats}_")
        changelog_lines.append("")
    
    # Default title if none found
    if not suggested_title:
        suggested_title = f"Release {new_version}"
    
    return '\n'.join(changelog_lines), suggested_title


def add_gitship_marker_to_commit_message(message: str) -> str:
    """Add the gitship marker to a commit message."""
    if GITSHIP_COMMIT_MARKER in message:
        return message
    return f"{message}\n\n{GITSHIP_COMMIT_MARKER}"