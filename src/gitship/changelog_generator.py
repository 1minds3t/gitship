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
    
    # Get commit SHAs and subjects - use %B for full message
    log_output = run_git([
        "log", range_str, 
        "--pretty=format:%H|||%s|||%B|||END_COMMIT",
        "--no-merges"  # We'll handle merges separately
    ], repo_path)
    
    commits = []
    seen_messages = set()
    
    # Split by END_COMMIT marker to handle multi-line bodies
    for commit_block in log_output.split('|||END_COMMIT'):
        if not commit_block.strip():
            continue
            
        parts = commit_block.split('|||')
        if len(parts) < 3:
            continue
            
        sha = parts[0].strip()
        subject = parts[1].strip()
        full_message = parts[2].strip() if len(parts) > 2 else ""
        
        # Body is everything after the subject line in the full message
        # The full message (%B) includes subject + blank line + body
        body_parts = full_message.split('\n', 1)
        body = body_parts[1].strip() if len(body_parts) > 1 else ""
        
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
        is_gitship = GITSHIP_COMMIT_MARKER in body or GITSHIP_COMMIT_MARKER in full_message
        
        commits.append({
            'sha': sha,
            'subject': subject,
            'body': body,
            'full_message': full_message,
            'is_gitship': is_gitship,
            'is_merge': False
        })
    
    # Also get merge commits (they often have detailed info)
    merge_log = run_git([
        "log", range_str,
        "--pretty=format:%H|||%s|||%B|||END_COMMIT",
        "--merges"
    ], repo_path)
    
    for commit_block in merge_log.split('|||END_COMMIT'):
        if not commit_block.strip():
            continue
            
        parts = commit_block.split('|||')
        if len(parts) < 3:
            continue
            
        sha = parts[0].strip()
        subject = parts[1].strip()
        full_message = parts[2].strip() if len(parts) > 2 else ""
        
        body_parts = full_message.split('\n', 1)
        body = body_parts[1].strip() if len(body_parts) > 1 else ""
        
        # Extract useful info from merge commits
        if "Merge pull request" in subject or "Merge branch" in subject:
            # Get the actual content from the merge body
            if body and not any(phrase in body.lower() for phrase in ["conflict", "auto-merge"]):
                commits.append({
                    'sha': sha,
                    'subject': body.split('\n')[0] if body else subject,
                    'body': body,
                    'full_message': full_message,
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
    current_section = None
    skip_next_empty = False
    
    for line in commit_body.split('\n'):
        original_line = line
        line = line.strip()
        
        # Skip the marker line
        if GITSHIP_COMMIT_MARKER in line:
            break
        
        # Detect section headers (any line ending with : that looks like a section)
        if line.endswith(':') and any(keyword in line for keyword in ['New files', 'Modified', 'Renames', 'Translations']):
            current_section = line
            lines.append(f"**{line}**")
            skip_next_empty = False
            continue
        
        # If we're in a section and line starts with bullet (• or -)
        if current_section and (line.startswith('•') or line.startswith('-')):
            # Remove the bullet and any leading whitespace
            content = line.lstrip('•-').strip()
            lines.append(f"- {content}")
            skip_next_empty = False
        elif current_section and not line:
            # Empty line might end the section, but don't add it to output
            if not skip_next_empty:
                current_section = None
        elif not current_section and line and not line.startswith('Update'):
            # This might be a title/summary line before sections - skip it
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
    
    # DEBUG: Show what commits we found
    print(f"[DEBUG] Found {len(commits)} commits since {last_tag}")
    
    # Get file statistics
    range_str = f"{last_tag}..HEAD" if last_tag else "HEAD"
    stats = run_git(["diff", "--shortstat", range_str], repo_path)
    
    changelog_lines = []
    suggested_title = ""
    
    # Prioritize gitship-generated commits for details
    gitship_commits = [c for c in commits if c['is_gitship']]
    other_commits = [c for c in commits if not c['is_gitship']]
    
    print(f"[DEBUG] Gitship commits: {len(gitship_commits)}, Other: {len(other_commits)}")
    
    # Collect ALL file changes from ALL commits to generate comprehensive changelog
    all_new_files = []
    all_modified_files = []
    all_renames = []
    
    # Also collect commit subjects for "Additional Changes" section
    commit_subjects = []
    
    # Analyze ALL gitship commits and extract their detailed file changes
    for commit in gitship_commits:
        body = commit['body']
        commit_subjects.append(commit['subject'])
        
        # Parse the structured sections from each commit
        current_section = None
        for line in body.split('\n'):
            line_stripped = line.strip()
            
            # Detect section headers
            if line_stripped.endswith(':'):
                if 'New files:' in line_stripped:
                    current_section = 'new'
                elif 'Modified:' in line_stripped:
                    current_section = 'modified'
                elif 'Renames:' in line_stripped:
                    current_section = 'renames'
                else:
                    current_section = None
                continue
            
            # Extract file entries
            if current_section and (line_stripped.startswith('•') or line_stripped.startswith('-')):
                content = line_stripped.lstrip('•-').strip()
                
                if current_section == 'new':
                    all_new_files.append(content)
                elif current_section == 'modified':
                    all_modified_files.append(content)
                elif current_section == 'renames':
                    all_renames.append(content)
    
    # Now get the ACTUAL diff stats from git for accurate LOC counts
    # This gives us the real total changes across all commits
    file_stats = {}
    try:
        diff_stat_output = run_git(["diff", "--stat", f"{last_tag}..HEAD"], repo_path)
        for line in diff_stat_output.split('\n'):
            if '|' in line:
                # Format: " filename | 123 +++++-----"
                parts = line.split('|')
                if len(parts) >= 2:
                    filename = parts[0].strip()
                    stat_part = parts[1].strip()
                    # Extract numbers
                    nums = stat_part.split()[0] if stat_part.split() else "0"
                    try:
                        changes = int(nums)
                        file_stats[filename] = changes
                    except:
                        pass
    except:
        pass
    
    # Generate smart title based on ALL changes
    all_files = set()
    for f in all_new_files:
        fname = f.split('(')[0].strip()
        all_files.add(fname)
    for f in all_modified_files:
        fname = f.split('(')[0].strip()
        all_files.add(fname)
    
    # Extract key module names
    key_modules = set()
    for filepath in all_files:
        parts = filepath.replace('.py', '').split('/')
        if parts:
            module = parts[-1]
            if module and module not in ['test', 'tests', '__init__', '__pycache__']:
                key_modules.add(module)
    
    key_files = sorted(list(key_modules))[:3]
    
    # Generate title
    if len(key_files) == 1:
        if all_new_files and all_modified_files:
            suggested_title = f"Add {key_files[0]} and improvements"
        elif all_new_files:
            suggested_title = f"Add {key_files[0]}"
        else:
            suggested_title = f"Fix {key_files[0]}"
    elif len(key_files) == 2:
        if all_new_files and all_modified_files:
            suggested_title = f"Add {key_files[0]}, fix {key_files[1]}"
        else:
            suggested_title = f"Fix {key_files[0]} and {key_files[1]}"
    elif len(key_files) >= 3:
        suggested_title = f"Fix {key_files[0]}, {key_files[1]}, and {key_files[2]}"
    else:
        suggested_title = commit_subjects[0] if commit_subjects else f"Release {new_version}"
        if ':' in suggested_title:
            suggested_title = suggested_title.split(':', 1)[1].strip()
    
    # Build comprehensive changelog with ALL changes
    changelog_lines = []
    
    # Categorize files by type for emoji display
    categorized = {
        'code': [],
        'tests': [],
        'docs': [],
        'config': [],
        'other': []
    }
    
    for filepath in all_files:
        fname_lower = filepath.lower()
        if 'test' in fname_lower:
            categorized['tests'].append(filepath)
        elif filepath.endswith(('.md', '.rst', '.txt')) or 'doc' in fname_lower:
            categorized['docs'].append(filepath)
        elif filepath.endswith(('.toml', '.yml', '.yaml', '.json', '.ini', '.cfg')):
            categorized['config'].append(filepath)
        elif filepath.endswith(('.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs')):
            categorized['code'].append(filepath)
        else:
            categorized['other'].append(filepath)
    
    # Add sections with emojis
    if categorized['code']:
        changelog_lines.append("**📝 Code Changes:**")
        for filepath in sorted(categorized['code']):
            # Try to get LOC from file_stats
            loc_info = ""
            if filepath in file_stats:
                loc_info = f" ({file_stats[filepath]} lines changed)"
            # Check if new or modified
            is_new = any(filepath in f for f in all_new_files)
            prefix = "NEW" if is_new else "UPDATE"
            changelog_lines.append(f"- {prefix}: {filepath}{loc_info}")
        changelog_lines.append("")
    
    if categorized['tests']:
        changelog_lines.append("**🧪 Tests:**")
        for filepath in sorted(categorized['tests']):
            loc_info = f" ({file_stats[filepath]} lines)" if filepath in file_stats else ""
            is_new = any(filepath in f for f in all_new_files)
            prefix = "NEW" if is_new else "UPDATE"
            changelog_lines.append(f"- {prefix}: {filepath}{loc_info}")
        changelog_lines.append("")
    
    if categorized['docs']:
        changelog_lines.append("**📚 Documentation:**")
        for filepath in sorted(categorized['docs']):
            loc_info = f" ({file_stats[filepath]} lines)" if filepath in file_stats else ""
            changelog_lines.append(f"- {filepath}{loc_info}")
        changelog_lines.append("")
    
    if categorized['config']:
        changelog_lines.append("**⚙️ Configuration:**")
        for filepath in sorted(categorized['config']):
            loc_info = f" ({file_stats[filepath]} lines)" if filepath in file_stats else ""
            changelog_lines.append(f"- {filepath}{loc_info}")
        changelog_lines.append("")
    
    # Add commit summaries
    if len(commit_subjects) > 1:
        changelog_lines.append("**Additional Changes:**")
        for subject in commit_subjects:
            changelog_lines.append(f"- {subject}")
        changelog_lines.append("")
    
    # Debug output
    print(f"[DEBUG] Using title: {suggested_title}")
    print(f"[DEBUG] Generated {len(changelog_lines)} changelog lines from {len(gitship_commits)} commits")
    
    # Add important non-gitship commits if any
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