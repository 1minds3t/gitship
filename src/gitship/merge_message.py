#!/usr/bin/env python3
"""
merge_message - Generate detailed merge commit messages using changelog analysis.

Uses the proven changelog_generator logic instead of reinventing the wheel.
"""

import subprocess
from pathlib import Path
from typing import Optional

try:
    from gitship.changelog_generator import run_git, get_all_commits_since_tag
except ImportError as e:
    # Fallback
    def run_git(args, cwd, check=False):
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    
    get_all_commits_since_tag = None


def generate_merge_message(
    repo_path: Path,
    base_ref: str,
    head_ref: str,
    pr_number: Optional[str] = None
) -> str:
    """
    Generate comprehensive merge commit message using changelog logic.
    
    Args:
        repo_path: Path to git repository
        base_ref: Base reference (e.g., main, or commit hash)
        head_ref: Head reference being merged in
        pr_number: Optional PR number
    """
    
    # Try to detect branch names if we got hashes
    base_name = base_ref
    head_name = head_ref
    
    # If full hash, try to get branch name
    if len(base_ref) == 40:
        base_name = base_ref[:7]
    if len(head_ref) == 40:
        head_name = head_ref[:7]
    
    # Get commit range
    range_str = f"{base_ref}..{head_ref}"
    
    # Analyze what actually changed to generate smart title
    changed_modules = set()
    area_changes = {}  # Track LOC per area
    
    numstat = run_git(["diff", "--numstat", range_str], repo_path)
    file_changes = []
    
    if numstat:
        for line in numstat.split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 3:
                adds = parts[0]
                dels = parts[1]
                filepath = parts[2]
                
                # Calculate total changes for this file
                try:
                    total_changes = (0 if adds == '-' else int(adds)) + (0 if dels == '-' else int(dels))
                except:
                    total_changes = 0
                
                # Categorize changes for smart title and track LOC
                if 'src/' in filepath:
                    path_parts = filepath.split('/')
                    if len(path_parts) >= 3:
                        area = path_parts[2].replace('.py', '').replace('_', '-')
                        # Skip 'locale' since it's tracked under 'i18n' below
                        if area not in ['__init__', '__pycache__', 'locale']:
                            if area not in area_changes:
                                area_changes[area] = 0
                            area_changes[area] += total_changes
                
                if 'test' in filepath.lower():
                    if 'tests' not in area_changes:
                        area_changes['tests'] = 0
                    area_changes['tests'] += total_changes
                    
                if 'locale' in filepath or 'i18n' in filepath or '.po' in filepath or '.mo' in filepath:
                    if 'i18n' not in area_changes:
                        area_changes['i18n'] = 0
                    area_changes['i18n'] += total_changes
                    
                if filepath.endswith('.md') and filepath != 'CHANGELOG.md':
                    if 'docs' not in area_changes:
                        area_changes['docs'] = 0
                    area_changes['docs'] += total_changes
                    
                if 'workflow' in filepath or '.github' in filepath:
                    if 'ci' not in area_changes:
                        area_changes['ci'] = 0
                    area_changes['ci'] += total_changes
                    
                if 'pyproject.toml' in filepath or 'setup' in filepath:
                    if 'config' not in area_changes:
                        area_changes['config'] = 0
                    area_changes['config'] += total_changes
                
                # Collect file changes
                if adds == '-' and dels == '-':
                    file_changes.append((filepath, 0, 0, True))
                else:
                    try:
                        file_changes.append((filepath, int(adds), int(dels), False))
                    except:
                        file_changes.append((filepath, 0, 0, False))
    
    # Generate smart title sorted by LOC
    if area_changes:
        # Sort areas by total LOC changes (largest first)
        sorted_areas = sorted(area_changes.items(), key=lambda x: x[1], reverse=True)
        
        areas_list = [area for area, _ in sorted_areas[:3]]  # Top 3 areas by LOC
        areas_str = ', '.join(areas_list)
        if len(sorted_areas) > 3:
            areas_str += f' (+{len(sorted_areas)-3} more)'
        title = f"Merge {head_name} → {base_name}: {areas_str}"
    else:
        title = f"Merge {head_name} → {base_name}"
    
    if pr_number:
        title += f" (#{pr_number})"
    
    lines = [title, ""]
    
    # Use changelog generator if available - SHOW CATEGORIZED COMMITS FIRST
    if get_all_commits_since_tag:
        # Get ALL commits in range (no dedup)
        commits = get_all_commits_since_tag(repo_path, base_ref)
        
        if commits:
            # Group by category and deduplicate
            features = []
            fixes = []
            docs = []
            tests = []
            other = []
            
            # Track repetitive commits by subject
            commit_groups = {}
            
            for commit in commits:
                subject = commit['subject']
                
                if subject not in commit_groups:
                    commit_groups[subject] = []
                commit_groups[subject].append(commit)
            
            # Now categorize, showing count for duplicates
            for subject, commit_list in commit_groups.items():
                subject_lower = subject.lower()
                
                # Build commit line
                if len(commit_list) == 1:
                    commit = commit_list[0]
                    commit_line = f"  • {subject} ({commit['sha'][:7]})"
                    
                    # Add body excerpt if available and meaningful
                    if commit.get('body') and len(commit['body']) > 10:
                        body_lines = commit['body'].split('\n')[:2]
                        for body_line in body_lines:
                            stripped = body_line.strip()
                            if stripped and not stripped.startswith('[') and not stripped.startswith('#'):
                                commit_line += f"\n    {stripped}"
                                break
                else:
                    # Multiple commits with same subject - show count only
                    commit_line = f"  • {subject} (×{len(commit_list)})"
                
                # Categorize
                if any(kw in subject_lower for kw in ['feat', 'feature', 'add']) and 'test' not in subject_lower:
                    features.append(commit_line)
                elif any(kw in subject_lower for kw in ['fix', 'bug', 'patch']):
                    fixes.append(commit_line)
                elif any(kw in subject_lower for kw in ['doc', 'readme']):
                    docs.append(commit_line)
                elif any(kw in subject_lower for kw in ['test', 'spec', 'concurrency']):
                    tests.append(commit_line)
                else:
                    other.append(commit_line)
            
            if features:
                lines.append("✨ Features:")
                lines.extend(features)
                lines.append("")
            
            if fixes:
                lines.append("🐛 Fixes:")
                lines.extend(fixes)
                lines.append("")
            
            if docs:
                lines.append("📚 Documentation:")
                lines.extend(docs)
                lines.append("")
            
            if tests:
                lines.append("🧪 Tests:")
                lines.extend(tests)
                lines.append("")
            
            if other:
                lines.append("📝 Other changes:")
                lines.extend(other)
                lines.append("")
    
    # Get summary stats AFTER categorized commits
    stats_out = run_git(["diff", "--shortstat", range_str], repo_path)
    if stats_out:
        lines.append(f"📊 {stats_out}")
        lines.append("")
    
    # Show detailed file-level stats LAST - ALL FILES, sorted by TOTAL LOC CHANGES
    if file_changes:
        # Sort by TOTAL CHANGES (additions + deletions), largest first
        file_changes.sort(key=lambda x: (x[1] + x[2]), reverse=True)
        
        # Categorize files for better organization
        translations = []
        config_files = []
        test_files = []
        source_files = []
        docs_files = []
        other_files = []
        
        for filepath, adds, dels, is_binary in file_changes:
            # Determine category
            if 'locale' in filepath or '.po' in filepath or '.mo' in filepath:
                category = translations
            elif 'test' in filepath.lower() or '/tests/' in filepath:
                category = test_files
            elif filepath.endswith('.md'):
                category = docs_files
            elif any(cfg in filepath for cfg in ['pyproject.toml', 'setup.py', 'setup.cfg', '.yml', '.yaml', 'meta']):
                category = config_files
            elif filepath.startswith('src/'):
                category = source_files
            else:
                category = other_files
            
            # Format line
            if is_binary:
                line = f"  • {filepath} (binary)"
            elif adds > 0 and dels > 0:
                line = f"  • {filepath}: +{adds} -{dels}"
            elif adds > 0 and dels == 0:
                line = f"  • {filepath}: +{adds}"
            elif dels > 0 and adds == 0:
                line = f"  • {filepath}: -{dels}"
            else:
                line = f"  • {filepath}"
            
            category.append(line)
        
        lines.append("📁 Detailed file changes:")
        
        # Show translations first if present
        if translations:
            lines.append("  Translations:")
            lines.extend(translations)
            lines.append("")
        
        # Show source files
        if source_files:
            lines.append("  Source code:")
            lines.extend(source_files)
            lines.append("")
        
        # Show test files
        if test_files:
            lines.append("  Tests:")
            lines.extend(test_files)
            lines.append("")
        
        # Show docs
        if docs_files:
            lines.append("  Documentation:")
            lines.extend(docs_files)
            lines.append("")
        
        # Show config files
        if config_files:
            lines.append("  Configuration:")
            lines.extend(config_files)
            lines.append("")
        
        # Show other files
        if other_files:
            lines.append("  Other:")
            lines.extend(other_files)
            lines.append("")
        
        lines.append("")
    
    # Add commit range footer
    first_commit = run_git(["rev-parse", "--short", base_ref], repo_path)
    last_commit = run_git(["rev-parse", "--short", head_ref], repo_path)
    if first_commit and last_commit:
        lines.append(f"Commits: {first_commit}..{last_commit}")
    
    return '\n'.join(lines)


def amend_last_commit_message(repo_path: Path, new_message: str) -> bool:
    """Amend the last commit with a new message."""
    try:
        result = subprocess.run(
            ["git", "commit", "--amend", "-m", new_message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False