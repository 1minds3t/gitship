#!/usr/bin/env python3
"""
releasegit - Interactive release automation tool.
Handles state recovery, smart changelogs, and release publication.
"""

import os
import sys
import re
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from collections import Counter
from typing import Optional

# Try to import changelog_generator for better changelog generation
try:
    from gitship.changelog_generator import (
        generate_detailed_changelog,
        analyze_uncommitted_changes,
        GITSHIP_COMMIT_MARKER
    )
    CHANGELOG_GENERATOR_AVAILABLE = True
except ImportError:
    CHANGELOG_GENERATOR_AVAILABLE = False

# --- ANSI COLORS ---
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

# --- GIT & SYSTEM HELPERS ---

def run_git(args, cwd=None, check=True):
    """Run git command and return stdout string."""
    try:
        res = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, check=check
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        if not check: return ""
        print(f"Git error: {' '.join(args)}\n{e.stderr}")
        sys.exit(1)

def get_current_version(repo_path: Path) -> str:
    toml = repo_path / "pyproject.toml"
    if not toml.exists(): return "0.0.0"
    content = toml.read_text()
    match = re.search(r'^version\s*=\s*"(.*?)"', content, re.MULTILINE)
    return match.group(1) if match else "0.0.0"

def get_pypi_latest_version(repo_path: Path) -> Optional[str]:
    """Get the latest version published on PyPI."""
    try:
        from . import pypi
        package_name = pypi.read_package_name(repo_path)
        
        if not package_name:
            return None
        
        import requests
        resp = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=5)
        
        if resp.status_code == 200:
            data = resp.json()
            # Get the latest version from PyPI
            latest = data.get('info', {}).get('version')
            return f"v{latest}" if latest else None
    except:
        pass
    
    return None

def show_review_before_changelog(repo_path: Path, from_ref: str, to_ref: str = "HEAD") -> bool:
    """
    Show interactive review of changes before generating changelog.
    Returns True if user wants to continue, False to cancel.
    """
    try:
        from gitship import review
        
        print(f"\n{Colors.CYAN}{Colors.BOLD}📊 COMMIT REVIEW{Colors.RESET}")
        print("=" * 80)
        print(f"{Colors.DIM}Reviewing changes to help write better release notes{Colors.RESET}")
        print()
        print(f"From: {Colors.YELLOW}{from_ref}{Colors.RESET}")
        print(f"To:   {Colors.GREEN}{to_ref}{Colors.RESET}")
        print()
        
        # Call review.main_with_args - it will display the review
        # Parameters: repo_path, from_ref, to_ref, export, export_path, stat_only
        review.main_with_args(
            repo_path=repo_path,
            from_ref=from_ref,
            to_ref=to_ref,
            export=False,
            export_path=None,
            stat_only=False
        )
        
        print()
        print("=" * 80)
        print(f"{Colors.BOLD}Ready to write release notes?{Colors.RESET}")
        print("  y - Continue to release notes editor")
        print("  n - Cancel release")
        print()
        
        try:
            choice = input("Continue? (y/n): ").strip().lower()
            return choice in ('y', 'yes', '')
        except (KeyboardInterrupt, EOFError):
            return False
            
    except ImportError:
        print(f"{Colors.YELLOW}⚠️  Review module not available, skipping review{Colors.RESET}")
        return True
    except Exception as e:
        print(f"{Colors.YELLOW}⚠️  Error showing review: {e}{Colors.RESET}")
        return True


def get_last_tag(repo_path: Path, prefer_pypi: bool = True) -> str:
    """
    Get the last release tag. 
    
    Args:
        prefer_pypi: If True, use PyPI version if available (avoids "no changes" 
                    when git tag exists but hasn't been reviewed yet)
    """
    if prefer_pypi:
        # Try PyPI first - this is what's actually published
        pypi_version = get_pypi_latest_version(repo_path)
        if pypi_version:
            print(f"{Colors.DIM}[Using PyPI latest: {pypi_version}]{Colors.RESET}")
            return pypi_version
    
    # Fallback to git tags
    try:
        return run_git(["describe", "--tags", "--abbrev=0"], cwd=repo_path, check=False)
    except SystemExit:
        return "" # No tags yet

def check_remote_tag(repo_path: Path, tag: str) -> bool:
    """Check if tag exists on origin."""
    res = run_git(["ls-remote", "origin", "refs/tags/" + tag], cwd=repo_path, check=False)
    return bool(res)

def check_pypi_version_exists(package_name: str, version: str) -> bool:
    """Check if a specific version exists on PyPI."""
    try:
        import requests
        ver = version.lstrip('v')
        resp = requests.get(f"https://pypi.org/pypi/{package_name}/{ver}/json", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def check_workflow_running(repo_path: Path, tag: str) -> tuple[bool, str]:
    """
    Check if the publish workflow is currently running or queued for this tag.
    Returns (is_running, human_readable_status).
    """
    if not shutil.which("gh"):
        return False, "gh not available"
    try:
        import json
        res = subprocess.run(
            ["gh", "run", "list", "--workflow=publish.yml",
             "--json", "status,conclusion,headBranch,displayTitle,url",
             "--limit", "10"],
            cwd=repo_path, capture_output=True, text=True
        )
        if res.returncode != 0:
            return False, "could not query runs"
        for run in json.loads(res.stdout or "[]"):
            status     = run.get("status", "")
            conclusion = run.get("conclusion", "")
            title      = run.get("displayTitle", "")
            branch     = run.get("headBranch", "")
            url        = run.get("url", "")
            if tag in title or tag in branch:
                if status in ("queued", "in_progress", "waiting", "requested"):
                    return True, f"{status} — {url}"
                if status == "completed":
                    return False, f"completed ({conclusion}) — {url}"
        return False, "no matching run found"
    except Exception as e:
        return False, f"error: {e}"


def get_gh_release_info(repo_path: Path, tag: str) -> dict:
    """
    Fetch full info about an existing GitHub release for this tag.
    Returns dict with keys: exists, is_draft, title, body.
    """
    empty = {"exists": False, "is_draft": False, "title": "", "body": ""}
    if not shutil.which("gh"):
        return empty
    try:
        import json
        res = subprocess.run(
            ["gh", "release", "view", tag,
             "--json", "isDraft,name,body,url"],
            cwd=repo_path, capture_output=True, text=True
        )
        if res.returncode != 0:
            return empty
        data = json.loads(res.stdout)
        return {
            "exists":   True,
            "is_draft": data.get("isDraft", False),
            "title":    data.get("name", ""),
            "body":     data.get("body", ""),
            "url":      data.get("url", ""),
        }
    except Exception:
        return empty


def get_repo_url(repo_path: Path) -> str:
    """Get the full GitHub repository URL (e.g. https://github.com/user/repo)."""
    try:
        # Method 1: Ask gh CLI (Most reliable)
        res = subprocess.run(["gh", "repo", "view", "--json", "url", "-q", ".url"], cwd=repo_path, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
            
        # Method 2: Parse git remote
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_path, capture_output=True, text=True)
        url = res.stdout.strip()
        if "github.com" in url:
            # Handle SSH: git@github.com:User/Repo.git -> User/Repo
            if "git@" in url: 
                path = url.split(":", 1)[1].replace(".git", "")
                return f"https://github.com/{path}"
            # Handle HTTPS: https://github.com/User/Repo.git -> User/Repo
            return url.replace(".git", "")
    except: pass
    
    return f"https://github.com/{repo_path.name}" # Fallback

def is_dirty(repo_path: Path) -> bool:
    """Check if relevant files are modified."""
    res = run_git(["status", "--porcelain"], cwd=repo_path)
    return "pyproject.toml" in res or "CHANGELOG.md" in res

def get_unpushed_commits(repo_path: Path) -> int:
    """Get count of commits ahead of origin/main."""
    try:
        # Fetch remote state without pulling
        run_git(["fetch", "origin"], cwd=repo_path, check=False)
        branch = get_current_branch(repo_path)
        res = run_git(["rev-list", "--count", f"origin/{branch}..HEAD"], cwd=repo_path, check=False)
        return int(res) if res else 0
    except:
        return 0

def has_translation_changes(repo_path: Path) -> bool:
    """Check if translation files are modified (unstaged)."""
    res = run_git(["status", "--porcelain"], cwd=repo_path)
    print(f"[DEBUG] has_translation_changes status output: {repr(res[:200] if res else 'EMPTY')}")
    if not res: return False
    
    lines = res.strip().split('\n')
    for line in lines:
        if not line.strip():
            continue
        # Status format is: "XY filename" where X=staged, Y=unstaged
        if len(line) >= 4:
            status_code = line[:2]
            filename = line[3:].strip()
            print(f"[DEBUG] Checking line: status='{status_code}' file='{filename}'")
            # Check if either position has M/D/A (staged OR unstaged)
            has_changes = any(c in ['M', 'D', 'A'] for c in status_code)
            is_translation = '/locale/' in filename and '.po' in filename
            
            if has_changes and is_translation:
                print(f"[DEBUG] FOUND translation change!")
                return True
    print(f"[DEBUG] No translation changes found")
    return False

# --- SMART CHANGELOG GENERATOR ---

def extract_changelog_section(repo_path: Path, version: str) -> str:
    """Extract changelog content for a specific version."""
    cl_path = repo_path / "CHANGELOG.md"
    if not cl_path.exists(): return ""
    content = cl_path.read_text()
    
    # Match "## [0.2.2] — 2026-02-14" (with em dash)
    # Capture everything until next ## or end of file
    pattern = rf"## \[{re.escape(version)}\][^\n]*\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""

def get_smart_changelog(repo_path: Path, last_tag: str, new_version: str) -> tuple[str, str]:
    """
    Generate changelog with proper title, grouped commits, and duplicate counting.
    Returns (draft_content, suggested_title)
    
    Now uses the shared changelog_generator module for better analysis when available.
    """
    # Use the new detailed changelog generator if available
    if CHANGELOG_GENERATOR_AVAILABLE:
        try:
            return generate_detailed_changelog(repo_path, last_tag, new_version)
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ Advanced changelog generation failed: {e}{Colors.RESET}")
            print(f"{Colors.DIM}Falling back to basic changelog...{Colors.RESET}")
    
    # FALLBACK: Basic implementation
    range_str = f"{last_tag}..HEAD" if last_tag else "HEAD"
    
    # Get file stats
    stats = ""
    try:
        stats_output = run_git([
            "diff", "--shortstat", range_str
        ], cwd=repo_path, check=False)
        
        if stats_output:
            stats = stats_output.strip()
    except:
        pass
    
    # Get commit list
    raw_log = run_git([
        "log", range_str, "--pretty=format:%s"
    ], cwd=repo_path, check=False)
    
    commit_list = []
    seen = set()
    
    for line in raw_log.splitlines():
        line = line.strip()
        if not line: 
            continue
        
        # Filter noise
        if line.startswith("chore: release"):
            continue
        if line.startswith("Merge"):
            continue
        if any(phrase in line.lower() for phrase in ["auto-merge", "sync main", "sync development"]):
            continue
        
        # Deduplicate
        if line in seen:
            continue
            
        seen.add(line)
        commit_list.append(line)
    
    # Build the changelog
    lines = []
    
    # Group and count commits
    features = []
    fixes = []
    refactors = []
    updates = {}  # Use dict to count duplicates
    other = []
    
    if commit_list:
        for commit in commit_list:
            if commit.startswith("feat"):
                features.append(commit)
            elif commit.startswith("fix"):
                fixes.append(commit)
            elif commit.startswith("refactor"):
                refactors.append(commit)
            elif commit.startswith("Update "):
                target = commit.replace("Update ", "").strip()
                updates[target] = updates.get(target, 0) + 1
            else:
                other.append(commit)
    
    # Generate a suggested title based on the most significant change
    suggested_title = ""
    # Determine suggested title based on what changed
    if features:
        suggested_title = features[0].split(':', 1)[1].strip() if ':' in features[0] else "New features and improvements"
    elif fixes:
        suggested_title = "Bug fixes and improvements"
    elif updates:
        suggested_title = "Code updates and improvements"
    else:
        suggested_title = f"Release v{new_version}"

    # Add main description sections
    if features:
        lines.append("**Features:**")
        for c in features:
            lines.append(f"- {c}")
        lines.append("")
    
    if fixes:
        lines.append("**Fixes:**")
        for c in fixes:
            lines.append(f"- {c}")
        lines.append("")
    
    if refactors:
        lines.append("**Refactoring:**")
        for c in refactors:
            lines.append(f"- {c}")
        lines.append("")
    
    if updates:
        lines.append("**Configuration Updates:**")
        sorted_updates = sorted(updates.items(), key=lambda x: x[1], reverse=True)
        for target, count in sorted_updates:
            if count > 1:
                lines.append(f"- Update {target} (x{count})")
            else:
                lines.append(f"- Update {target}")
        lines.append("")
    
    if other:
        lines.append("**Other Changes:**")
        for c in other:
            lines.append(f"- {c}")
        lines.append("")
        
    # Add file stats
    if stats:
        lines.append(f"_{stats}_")
        lines.append("")
    
    return "\n".join(lines), suggested_title

def edit_notes(new_ver: str, draft: str, suggested_title: str, pkg_name: str = "") -> tuple[str, str]:
    """
    Enhanced interactive release notes editor with proper markdown support.
    Returns (final_notes_for_changelog, final_notes_for_github, release_title_suffix)
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n{Colors.CYAN}{Colors.BOLD}📝 RELEASE NOTES BUILDER{Colors.RESET}")
    print("=" * 80)
    print()
    
    # Build automatic prefix
    prefix = f"{pkg_name} v{new_ver}" if pkg_name else f"v{new_ver}"
    
    # Step 1: Choose release title suffix
    print(f"{Colors.BOLD}Step 1: Release Title{Colors.RESET}")
    print(f"{Colors.DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.RESET}")
    print()
    print(f"{Colors.CYAN}Auto prefix:{Colors.RESET} {Colors.BRIGHT_CYAN}{prefix}{Colors.RESET}")
    print(f"{Colors.CYAN}Suggested suffix:{Colors.RESET} {Colors.GREEN}{suggested_title}{Colors.RESET}")
    print()
    print(f"{Colors.BOLD}Full title will be:{Colors.RESET} {Colors.BRIGHT_CYAN}{prefix}{Colors.RESET} - {Colors.GREEN}[YOUR SUFFIX]{Colors.RESET}")
    print()
    print(f"{Colors.YELLOW}Enter your suffix (or press Enter to use suggested):{Colors.RESET}")
    
    try:
        user_suffix = input(f"{Colors.BRIGHT_BLUE}Suffix:{Colors.RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nRelease cancelled.")
        sys.exit(1)
    
    final_suffix = user_suffix if user_suffix else suggested_title
    if not final_suffix:
        final_suffix = "Release"
    
    print(f"  → Title: {Colors.BRIGHT_CYAN}{prefix}{Colors.RESET} - {Colors.GREEN}{final_suffix}{Colors.RESET}")
    print()
    
    # Step 2: Show auto-generated preview first
    print(f"{Colors.BOLD}Step 2: Review Auto-Generated Content{Colors.RESET}")
    print("=" * 80)
    print(f"{Colors.DIM}This will be appended after any custom notes you write:{Colors.RESET}")
    print()
    print(draft)
    print("=" * 80)
    print()
    
    # Step 3: Choose what to do with release notes
    print(f"{Colors.BOLD}Step 3: Add Custom Notes (Optional){Colors.RESET}")
    print("Options:")
    print("  1. Open editor to write custom notes (auto-generated appended below)")
    print("  2. Use auto-generated notes only")
    print("  3. Cancel")
    print()
    
    detailed_notes = ""
    
    while True:
        try:
            notes_choice = input("Choose (1-3): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nRelease cancelled.")
            sys.exit(1)
        
        if notes_choice == '3':
            print("Release cancelled.")
            sys.exit(1)
        
        elif notes_choice == '2':
            # Skip detailed notes, use auto-generated only
            print(f"  → {Colors.GREEN}Using auto-generated notes{Colors.RESET}")
            break
        
        elif notes_choice == '1':
            # Open editor for custom notes
            template = f"""# Write your custom release notes here.
# Lines starting with "# " are comments and will be stripped.
# Markdown headers (## Heading) are preserved.
# The auto-generated breakdown will be appended below your notes.
#
# Example (all commented out — replace with your own content):
#
# ## 🚀 Major Feature Release: The "Atomic Workflow" Engine
# This release transforms the project into a robust platform.
#
# ### 🌟 Key Highlights
# - **New Engine:** All operations now use a safety layer...
# - **Interactive Suite:** A new guided conflict resolution workflow...
#
# ------------------------------------------------------------------
# Start writing below this line (delete these comments when done)
# ------------------------------------------------------------------

"""
            
            editor = os.environ.get('EDITOR', 'nano')
            with tempfile.NamedTemporaryFile(suffix=".md", mode='w+', delete=False) as tf:
                tf.write(template)
                tf_path = tf.name
            
            try:
                subprocess.call([editor, tf_path])
                with open(tf_path) as f:
                    content = f.read()
                
                # Remove ONLY comment lines (lines starting with "# " - hash + space)
                # This preserves markdown headers like ## Heading
                lines = []
                for line in content.splitlines():
                    stripped = line.lstrip()
                    # Remove only if it starts with "# " (comment), not "##" or "###" (markdown header)
                    if stripped.startswith("#") and not stripped.startswith("## "):
                        continue
                    # Stop at separator
                    if "----------------------------" in line or "============================" in line:
                        break
                    lines.append(line.rstrip())
                
                # Remove leading/trailing empty lines
                while lines and not lines[0]:
                    lines.pop(0)
                while lines and not lines[-1]:
                    lines.pop()
                
                detailed_notes = '\n'.join(lines)
                
                if detailed_notes:
                    print(f"  → {Colors.GREEN}Custom notes captured{Colors.RESET}")
                else:
                    print(f"  → {Colors.YELLOW}No custom notes, will use auto-generated{Colors.RESET}")
                
            except Exception as e:
                print(f"{Colors.RED}Error opening editor: {e}{Colors.RESET}")
                print("Falling back to auto-generated notes only")
            finally:
                try:
                    os.unlink(tf_path)
                except:
                    pass
            
            break
        else:
            print(f"{Colors.RED}Invalid choice{Colors.RESET}")
            continue
    
    print()
    
    # Build final notes - TWO VERSIONS
    # Version 1: For CHANGELOG.md (includes header + title line)
    changelog_parts = []
    changelog_parts.append(f"## [{new_ver}] — {date_str}")
    changelog_parts.append("")
    changelog_parts.append(final_suffix)
    changelog_parts.append("")
    
    if detailed_notes:
        changelog_parts.append(detailed_notes)
        changelog_parts.append("")
        changelog_parts.append("---")
        changelog_parts.append("")
    
    if draft:
        changelog_parts.append(draft)
    
    changelog_final = '\n'.join(changelog_parts)
    
    # Version 2: For GitHub Release (NO header, NO title line - those are separate fields)
    github_parts = []
    
    if detailed_notes:
        github_parts.append(detailed_notes)
        github_parts.append("")
        github_parts.append("---")
        github_parts.append("")
    
    if draft:
        github_parts.append(draft)
    
    github_final = '\n'.join(github_parts).strip()
    
    # Preview both
    print("=" * 80)
    print(f"{Colors.BOLD}CHANGELOG.md PREVIEW{Colors.RESET}")
    print("=" * 80)
    print(f"{Colors.CYAN}{changelog_final}{Colors.RESET}")
    print()
    print("=" * 80)
    print(f"{Colors.BOLD}GITHUB RELEASE BODY PREVIEW{Colors.RESET}")
    print(f"{Colors.DIM}(Title and version header are separate fields){Colors.RESET}")
    print("=" * 80)
    print(f"{Colors.CYAN}{github_final}{Colors.RESET}")
    print("=" * 80)
    print()
    
    return changelog_final, github_final, final_suffix

def write_changelog(repo_path: Path, notes: str, version: str):
    """
    Write changelog entry with proper title format.
    """
    cl = repo_path / "CHANGELOG.md"
    
    # POST-PROCESS: Add header if missing, with em dash
    if not notes.strip().startswith(f"## [{version}]"):
        date_str = datetime.now().strftime("%Y-%m-%d")
        # Use em dash (—) for title format
        notes = f"## [{version}] — {date_str}\n\n{notes}"
    
    header_block = """# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

"""
    
    # Create file if doesn't exist
    if not cl.exists():
        cl.write_text(f"{header_block}{notes}\n")
        return

    content = cl.read_text()
    
    # Remove any existing entry for this version
    version_pattern = rf"^## \[{re.escape(version)}\].*?(?=^## \[|\Z)"
    cleaned = re.sub(version_pattern, "", content, flags=re.MULTILINE | re.DOTALL).strip()
    
    # Ensure header exists
    if "# Changelog" not in cleaned:
        cleaned = header_block.strip()
    
    # Find where to insert (after header)
    lines = cleaned.split('\n')
    
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("## ["):
            insert_at = i
            break
    
    # Insert the new version
    if insert_at < len(lines):
        new_lines = lines[:insert_at] + ['', notes.rstrip(), ''] + lines[insert_at:]
    else:
        new_lines = lines + ['', notes.rstrip()]
    
    # Clean up excessive blank lines
    result = '\n'.join(new_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    cl.write_text(result.rstrip() + '\n')

def handle_translation_stash(repo_path: Path) -> bool:
    """Stash translation files if they're the only changes. Returns True if stashed."""
    if not has_translation_changes(repo_path):
        return False
    
    print("\n⚠️  Translation files (.po) detected as only uncommitted changes.")
    print("These will be stashed before push and restored after.")
    
    run_git(["stash", "push", "-m", "Auto-stash: translation files before release"], cwd=repo_path)
    print("✓ Translation files stashed")
    return True

def atomic_stash_and_run(repo_path: Path, git_command: list, description: str):
    """
    Atomically stash translations, run git command, then restore.
    This prevents the AI translator from writing more changes between stash and command.
    """
    print(f"\n[DEBUG] atomic_stash_and_run called for: {description}")
    print(f"[DEBUG] Command: git {' '.join(git_command)}")
    
    # Check if we need to stash RIGHT NOW
    needs_stash = has_translation_changes(repo_path)
    print(f"[DEBUG] Translation changes detected: {needs_stash}")
    
    if needs_stash:
        print(f"\n🔒 Stashing translations immediately before {description}...")
        stash_result = subprocess.run(
            ["git", "stash", "push", "-m", f"Auto-stash before {description}"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if stash_result.returncode == 0:
            print("✓ Stashed")
        else:
            print(f"[DEBUG] Stash failed: {stash_result.stderr}")
    
    # Immediately run the command
    print(f"[DEBUG] Running git command...")
    result = subprocess.run(
        ["git"] + git_command,
        cwd=repo_path,
        capture_output=True,
        text=True
    )
    print(f"[DEBUG] Command exit code: {result.returncode}")
    if result.returncode != 0:
        print(f"[DEBUG] Command stderr: {result.stderr}")
        print(f"[DEBUG] Command stdout: {result.stdout}")
    
    # Restore if we stashed
    if needs_stash:
        stash_list = subprocess.run(
            ["git", "stash", "list"],
            cwd=repo_path,
            capture_output=True,
            text=True
        ).stdout
        
        if f"Auto-stash before {description}" in stash_list:
            print(f"↩️  Restoring translations after {description}...")
            pop_result = subprocess.run(
                ["git", "stash", "pop"],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            if pop_result.returncode == 0:
                print("✓ Restored")
            else:
                print(f"[DEBUG] Stash pop had issues: {pop_result.stderr}")
    
    return result

def restore_translation_stash(repo_path: Path):
    """Restore stashed translation files."""
    stash_list = run_git(["stash", "list"], cwd=repo_path, check=False)
    if "Auto-stash: translation files before release" in stash_list:
        print("\n↩️  Restoring translation files from stash...")
        result = subprocess.run(
            ["git", "stash", "pop"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("✓ Translation files restored")
        else:
            # Stash pop failed, maybe conflict
            if "conflict" in result.stderr.lower():
                print("⚠️  Stash had conflicts, keeping stash. Run 'git stash pop' manually later.")
            else:
                print("⚠️  Stash restore had issues (may already be applied)")
    else:
        # No stash to restore, likely already popped or never created
        pass

def reset_version_to_tag(repo_path: Path):
    """Reset pyproject.toml version to match latest git tag."""
    print("\n🔄 VERSION RESET")
    print("=" * 60)
    
    # Get latest tag
    try:
        latest_tag = run_git(["describe", "--tags", "--abbrev=0"], cwd=repo_path)
        latest_version = latest_tag.lstrip('v')
    except SystemExit:
        print("❌ No git tags found")
        return
    
    # Get current TOML version
    current_version = get_current_version(repo_path)
    
    print(f"  Current TOML: {current_version}")
    print(f"  Latest tag:   {latest_tag} ({latest_version})")
    
    if current_version == latest_version:
        print("\n✓ Versions already match!")
        return
    
    confirm = input(f"\nReset TOML version to {latest_version}? (y/n): ").strip().lower()
    
    if confirm != 'y':
        print("Cancelled")
        return
    
    # Update pyproject.toml
    toml_path = repo_path / "pyproject.toml"
    content = toml_path.read_text()
    
    # Replace version line
    new_content = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{latest_version}"',
        content,
        flags=re.MULTILINE
    )
    
    toml_path.write_text(new_content)
    
    # Commit the change
    run_git(["add", "pyproject.toml"], cwd=repo_path)
    run_git(["commit", "-m", f"Reset version to {latest_version}"], cwd=repo_path)
    
    print(f"\n✓ Reset version to {latest_version}")
    print("✓ Committed change")
    
    push = input("\nPush change? (y/n): ").strip().lower()
    if push == 'y':
        run_git(["push"], cwd=repo_path)
        print("✓ Pushed")


def get_current_branch(repo_path: Path) -> str:
    """Get the current git branch name."""
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, check=False) or "main"


def _validate_pypi_version(v: str) -> bool:
    """
    Basic PEP 440 sanity check - not exhaustive but catches obvious garbage.
    Allows: 1.2.3  /  2026.21441  /  2026.21441.1  /  1.0.0a1  /  1.0.0.post1
    """
    import re
    return bool(re.match(
        r'^[0-9]+(\.[0-9a-zA-Z]+)*([._-]?(a|b|rc|alpha|beta|post|dev)[0-9]*)?$',
        v.strip()
    ))


def _validate_git_tag(t: str) -> bool:
    """
    Git tag rules: no spaces, no ~^:?*[ backslash, no leading dot or dash, not ..
    """
    import re
    if not t or len(t) > 250:
        return False
    if re.search(r'[ ~^:?*\x00-\x1f\x7f]', t):
        return False
    if '[' in t or '\\\\' in t:
        return False
    if t.startswith('.') or t.startswith('-'):
        return False
    if '..' in t or t.endswith('.'):
        return False
    return True


def _build_tag_name(version: str, branch: str) -> str:
    """
    Build the correct tag name based on version scheme and branch.

    CVE scheme  (YYYY.CVENUM  or  YYYY.CVENUM.patch):
        main / lts-dispatcher  ->  CVE-YYYY-CVENUM          e.g. CVE-2026-21441
        lts-py37 / lts-py38   ->  CVE-YYYY-CVENUM-lts-py37  e.g. CVE-2026-21441-lts-py37
        with patch suffix     ->  CVE-2026-21441.1-lts-py37

    Semver / anything else    ->  v{version}
    """
    import re
    m = re.match(r'^(20\d\d)\.(\d{5,})(?:\.(\d+))?$', version)
    if not m:
        return f"v{version}"
    year, cve_num, patch = m.group(1), m.group(2), m.group(3)
    base_tag = f"CVE-{year}-{cve_num}"
    suffix   = f".{patch}" if patch else ""
    is_main  = branch in ("main", "lts-dispatcher", "master")
    return f"{base_tag}{suffix}" if is_main else f"{base_tag}{suffix}-{branch}"

def perform_git_release(repo_path: Path, version: str, release_title: str = "", github_notes: str = "", custom_tag: str = ""):
    # -- Detect branch and build tag -------------------------------------------
    current_branch = get_current_branch(repo_path)
    tag = custom_tag if custom_tag else _build_tag_name(version, current_branch)

    print(f"\n  Branch : {current_branch}")
    print(f"  Tag    : {tag}")

    # Check remote first
    if check_remote_tag(repo_path, tag):
        print(f"\n❌ Error: Tag {tag} already exists on remote!")
        print("Run 'git fetch --tags' to sync, or bump to a higher version.")
        return

    print(f"\nPreparing to release {tag}...")

    # Get ALL modified files (excluding translations)
    status_output = run_git(["status", "--porcelain"], cwd=repo_path)
    files_to_add = ["pyproject.toml", "CHANGELOG.md"]

    for line in status_output.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Split on whitespace - format is: "STATUS filename"
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            status_code, filename = parts
            # Exclude translations and files already added
            if '/locale/' not in filename and filename not in files_to_add:
                files_to_add.append(filename)

    print(f"  ✓ Staging {len(files_to_add)} files: {', '.join(files_to_add[:5])}{'...' if len(files_to_add) > 5 else ''}")

    # Add all relevant files
    for f in files_to_add:
        run_git(["add", f], cwd=repo_path)

    print(f"  ✓ Staged {len(files_to_add)} files")
    
    # Commit
    try:
        run_git(["commit", "-m", f"chore: release {tag}"], cwd=repo_path)
        print(f"✓ Committed release {tag}")
    except:
        print("  (Nothing to commit, proceeding...)")
    
    # Check if we need to pull (only if not already rebased)
    unpushed_check = get_unpushed_commits(repo_path)
    if unpushed_check > 0:
        print(f"\n🔄 You have {unpushed_check} unpushed commits, checking for remote updates...")
        
        # ATOMIC: Stash right before pull to prevent AI translator interference
        result = atomic_stash_and_run(repo_path, ["pull", "origin", current_branch, "--rebase"], "pull rebase")
        
        if result.returncode != 0:
            # Check if it's a conflict
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                print("\n🚨 MERGE CONFLICTS DETECTED during rebase!")
                
                # Get conflicted files
                conflicts = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
                conflict_files = [f for f in conflicts.split('\n') if f.strip()]
                
                if conflict_files:
                    print(f"\n   Conflicted files ({len(conflict_files)}):")
                    for f in conflict_files:
                        print(f"     - {f}")
                    
                    print("\nWhat would you like to do?")
                    print("  1. RESOLVE - Resolve conflicts now")
                    print("  2. ABORT   - Abort rebase and exit")
                    
                    choice = input("\nChoice (1-2): ").strip()
                    
                    if choice == '1':
                        # Launch resolver
                        resolver_path = Path(__file__).parent / "resolve_conflicts.py"
                        if resolver_path.exists():
                            print("\n🔧 Launching conflict resolver...")
                            subprocess.call(["python3", str(resolver_path)], cwd=repo_path)
                        else:
                            # Simple fallback
                            for f in conflict_files:
                                print(f"\n📁 {f}")
                                print("  O - OURS (local) | T - THEIRS (remote)")
                                fc = input("Choice (O/T): ").strip().upper()
                                if fc == 'O':
                                    run_git(["checkout", "--ours", f], cwd=repo_path)
                                elif fc == 'T':
                                    run_git(["checkout", "--theirs", f], cwd=repo_path)
                                run_git(["add", f], cwd=repo_path)
                        
                        # Check if resolved
                        remaining = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
                        if remaining.strip():
                            print("\n⚠️  Still have unresolved conflicts. Resolve and re-run.")
                            sys.exit(1)
                        
                        # Continue rebase ATOMICALLY (stash right before)
                        print("\n🔄 Continuing rebase...")
                        result = atomic_stash_and_run(repo_path, ["rebase", "--continue"], "rebase continue")
                        
                        if result.returncode != 0:
                            # Check if NEW conflicts appeared
                            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                                print("\n🚨 NEW CONFLICTS appeared during rebase!")
                                print("   The rebase progressed but hit conflicts in another commit.")
                                print("\n   Looping back to conflict resolution...")
                                # LOOP BACK by recursively calling main logic
                                return _main_logic(repo_path)
                            else:
                                print("⚠️  Rebase continue failed:")
                                print(result.stdout)
                                print(result.stderr)
                                print("\nResolve issues and re-run gitship releasegit")
                                sys.exit(1)
                    else:
                        run_git(["rebase", "--abort"], cwd=repo_path)
                        print("✓ Rebase aborted")
                        sys.exit(1)
            else:
                print("⚠️  Pull had issues (non-conflict), proceeding...")
        else:
            print("✓ Pull complete, rebased on remote")
    else:
        print("\n✓ Already up to date with remote")
    
    # CRITICAL: Push commits FIRST, then tags (with atomic stashing)
    print(f"\n📤 Step 1/2: Pushing commits to origin/{current_branch}...")
    result = atomic_stash_and_run(repo_path, ["push", "origin", current_branch], "push commits")
    
    if result.returncode != 0:
        print("\n❌ COMMIT PUSH FAILED - ABORTING RELEASE")
        print("   Tag will NOT be created/pushed to prevent orphaned tags.")
        print(f"   Error: {result.stderr}")
        print("   Recommendation:")
        print("     1. Run 'git pull --rebase' to sync with remote")
        print("     2. Resolve any conflicts")
        print("     3. Re-run gitship releasegit")
        sys.exit(1)
    
    print("✓ Commits pushed!")
    
    # Only create and push tag if commits pushed successfully
    print(f"\n🏷️  Step 2/2: Creating and pushing tag {tag}...")
    try:
        run_git(["tag", "-a", tag, "-m", f"Release {tag}"], cwd=repo_path)
        print(f"  ✓ Created tag {tag}")
    except:
        print(f"  ! Tag {tag} already exists locally, using existing tag")

    try:
        run_git(["push", "origin", tag], cwd=repo_path)
        print(f"  ✓ Pushed tag {tag}")
    except SystemExit:
        print(f"\n⚠️  Tag push failed.")
        print(f"   Your commits are safe on remote, but tag may need manual push:")
        print(f"     git push origin {tag}")
        sys.exit(1)
    
    # Restore stashed translations
    print("\n🎉 Release complete!")
    print(f"\n✓ Tagged {tag}")
    print(f"✓ Changes pushed")
    
    # PyPI Publishing
    username = run_git(["config", "user.name"], cwd=repo_path, check=False) or "your-username"
    
    # Use the provided github_notes if available, otherwise extract from changelog
    if github_notes:
        changelog_content = github_notes
        print(f"[DEBUG] Using provided GitHub notes: {len(changelog_content)} chars")
    else:
        # Fallback: Extract changelog using the proper extraction function
        # NOTE: extract_changelog_section expects version without 'v' prefix
        version_no_v = tag.lstrip('v')
        changelog_content = extract_changelog_section(repo_path, version_no_v)
        if not changelog_content:
            # Fallback: try to read the first section manually
            cl_path = repo_path / "CHANGELOG.md"
            if cl_path.exists():
                content = cl_path.read_text()
                parts = content.split("## [")
                if len(parts) > 1:
                    changelog_content = ("## [" + parts[1]).split("## [")[0].strip()
        print(f"[DEBUG] Extracted from changelog: {len(changelog_content)} chars")
    
    print(f"[DEBUG] changelog preview: {changelog_content[:200] if changelog_content else 'EMPTY'}")
    
    # Try to determine release title if not provided
    if not release_title and changelog_content:
        # Try to extract the first line if it's not a header
        first_line = changelog_content.strip().splitlines()[0]
        if first_line and not first_line.startswith("**") and not first_line.startswith("#"):
            release_title = first_line.strip()
    
    # Call PyPI handler (which now handles GH release creation)
    from gitship import pypi
    pypi.handle_pypi_publishing(
        repo_path=repo_path,
        version=tag,
        changelog=changelog_content,
        username=username,
        title_suffix=release_title
    )

def _recommend_bump_type(repo_path: Path, last_tag: str) -> tuple[str, str]:
    """
    Analyze the diff since last_tag and recommend a semver bump type.
    Returns (bump_type, reasoning).
    """
    from_ref = last_tag or get_first_commit_ref(repo_path)
    
    # Show the shortstat first so user can see it
    shortstat = run_git(
        ["diff", "--shortstat", f"{from_ref}...HEAD"],
        cwd=repo_path, check=False
    )
    commit_messages = run_git(
        ["log", f"{from_ref}..HEAD", "--pretty=format:%s"],
        cwd=repo_path, check=False
    )
    num_commits = len([l for l in commit_messages.splitlines() if l.strip()])
    
    if shortstat:
        print(f"\n{Colors.CYAN}📊 Changes since {from_ref}:{Colors.RESET}")
        print(f"   {shortstat}")
        print(f"   {num_commits} commit(s)")
        print()

    # Parse shortstat: "X files changed, Y insertions(+), Z deletions(-)"
    files_changed = int(re.search(r'(\d+) file', shortstat or '').group(1) if re.search(r'(\d+) file', shortstat or '') else 0)
    insertions   = int(re.search(r'(\d+) insertion', shortstat or '').group(1) if re.search(r'(\d+) insertion', shortstat or '') else 0)
    deletions    = int(re.search(r'(\d+) deletion', shortstat or '').group(1) if re.search(r'(\d+) deletion', shortstat or '') else 0)
    total_lines  = insertions + deletions

    # Check commit messages for breaking change signals
    messages_lower = commit_messages.lower()
    has_breaking = any(kw in messages_lower for kw in [
        'breaking', 'breaking change', '!:', 'removed', 'remove ', 'drop ', 'incompatible'
    ])
    has_features = any(kw in messages_lower for kw in [
        'feat:', 'feature:', 'add ', 'added', 'new ', 'support '
    ])

    # Decision logic
    if has_breaking:
        return 'major', f"{num_commits} commit(s) — breaking change signals detected ({files_changed} files, {total_lines} lines)"
    elif has_features or total_lines > 100 or num_commits > 5:
        return 'minor', f"{num_commits} commits, {total_lines} lines — new functionality detected"
    else:
        return 'patch', f"{num_commits} commit(s), {total_lines} lines — looks like a small fix/change"

# --- MAIN FLOW ---

def main_with_repo(repo_path: Path):
    try:
        _main_logic(repo_path)
    except KeyboardInterrupt:
        print("\n\n⛔ Operation cancelled by user.")
        sys.exit(130)

def _main_logic(repo_path: Path):
    # Auto-scan dependencies before starting release
    try:
        from gitship.deps import check_and_update_deps
        print(f"\n{Colors.DIM}Scanning dependencies...{Colors.RESET}")
        check_and_update_deps(repo_path, silent=True)
    except ImportError:
        pass

    print(f"\n⚓ GITSHIP RELEASE: {repo_path.name}")
    print("=" * 60)
    
    # Check for uncommitted changes FIRST
    if CHANGELOG_GENERATOR_AVAILABLE:
        changes = analyze_uncommitted_changes(repo_path)
        if changes and changes['total'] > 0:
            print(f"\n{Colors.YELLOW}⚠️  Uncommitted Changes Detected!{Colors.RESET}")
            print(f"   Staged: {len(changes['staged'])} files")
            print(f"   Unstaged: {len(changes['unstaged'])} files")
            print(f"   Untracked: {len(changes['untracked'])} files")
            print(f"\n{Colors.BOLD}Recommendation:{Colors.RESET} Commit changes before releasing for better changelog.")
            print("\nOptions:")
            print(f"  1. {Colors.GREEN}COMMIT NOW{Colors.RESET} - Use 'gitship commit' to create detailed commit")
            print(f"  2. {Colors.YELLOW}CONTINUE ANYWAY{Colors.RESET} - Release without committing (not recommended)")
            print(f"  3. {Colors.RED}CANCEL{Colors.RESET} - Exit and commit manually")
            
            choice = input("\nChoice (1-3): ").strip()
            
            if choice == '1':
                # Run the commit tool
                print(f"\n{Colors.CYAN}Launching commit tool...{Colors.RESET}\n")
                try:
                    from gitship import commit as commit_module
                    commit_module.main_with_repo(repo_path)
                    print(f"\n{Colors.GREEN}Returning to release process...{Colors.RESET}\n")
                except Exception as e:
                    print(f"{Colors.RED}Error running commit tool: {e}{Colors.RESET}")
                    return
            elif choice == '3':
                print("Release cancelled.")
                return
            # If choice == '2', continue with uncommitted changes
    
    # --- CRITICAL: Ensure PyPI workflow exists BEFORE doing anything else ---
    # This prevents releasing without the ability to publish
    from . import pypi
    package_name = pypi.read_package_name(repo_path)
    if package_name:
        # Check/Create workflow silently or interactively based on existence
        workflow_path = repo_path / ".github" / "workflows" / "publish.yml"
        if not workflow_path.exists():
            print(f"\n{Colors.YELLOW}⚠️  Missing PyPI publish workflow! Creating it now...{Colors.RESET}")
            pypi.ensure_publish_workflow(repo_path, package_name)
            # If created, it's already staged by ensure_publish_workflow
        elif workflow_path.exists():
            # Check if outdated
            content = workflow_path.read_text()
            if "@v1.8.11" in content:
                print(f"\n{Colors.YELLOW}⚠️  Workflow has BROKEN version (@v1.8.11 causes metadata errors){Colors.RESET}")
                fix = input("Fix now? (y/n): ").strip().lower()
                if fix == 'y':
                    content = content.replace("@v1.8.11", "@release/v1")
                    workflow_path.write_text(content)
                    run_git(["add", str(workflow_path)], cwd=repo_path)
                    run_git(["commit", "-m", "fix: update pypi-publish to @release/v1"], cwd=repo_path)
                    run_git(["push"], cwd=repo_path)
                    print(f"{Colors.GREEN}✓ Fixed and pushed{Colors.RESET}")
    
    # CRITICAL: Check if already in rebase/merge state FIRST
    git_dir = repo_path / ".git"
    in_rebase = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
    in_merge = (git_dir / "MERGE_HEAD").exists()
    
    if in_rebase or in_merge:
        print("🚨 REBASE/MERGE IN PROGRESS DETECTED")
        print("   You have an unfinished rebase or merge.")
        
        # Check for MERGE conflicts (unmerged paths)
        conflicts = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
        all_conflict_files = [f for f in conflicts.split('\n') if f.strip()]
        
        # AUTO-RESOLVE translation file conflicts (always THEIRS) - don't bother user
        translation_conflicts = [f for f in all_conflict_files if '/locale/' in f and '.po' in f]
        real_conflicts = [f for f in all_conflict_files if f not in translation_conflicts]
        
        if translation_conflicts:
            print(f"\n🔄 Auto-resolving {len(translation_conflicts)} translation file conflict(s) with THEIRS...")
            for f in translation_conflicts:
                run_git(["checkout", "--theirs", f], cwd=repo_path, check=False)
                run_git(["add", f], cwd=repo_path, check=False)
            print("✓ Translation conflicts auto-resolved")
        
        conflict_files = real_conflicts  # Only show real conflicts to user
        
        # Check for ALL uncommitted changes (staged + unstaged) that will block rebase
        # git status --porcelain shows both
        status_output = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
        uncommitted_files = []
        for line in status_output.split('\n'):
            if line.strip():
                # Parse status format: "XY filename" where X is staged, Y is unstaged
                # We care about ANY changes (staged or unstaged)
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    status_code, filename = parts
                    # Skip translation files - handle separately
                    if '/locale/' in filename and '.po' in filename:
                        continue
                    # Skip if it's a staged change for files already in the commit
                    # We only care about unstaged modifications (M in second position)
                    if len(status_code) >= 2 and status_code[1] in ['M', 'D', 'A']:
                        uncommitted_files.append(filename)
        
        print(f"[DEBUG] Real conflicts (non-translation): {len(conflict_files)}")
        print(f"[DEBUG] Uncommitted changes (non-translation): {len(uncommitted_files)}")
        if uncommitted_files:
            print(f"[DEBUG] Uncommitted files: {uncommitted_files}")
        
        # If we auto-resolved translation conflicts and there are NO other issues, auto-continue
        if translation_conflicts and not conflict_files and not uncommitted_files:
            print("\n✓ Only translation conflicts detected - auto-continuing rebase...")
            result = atomic_stash_and_run(repo_path, ["rebase", "--continue"], "rebase continue")
            
            if result.returncode == 0:
                print("✓ Rebase continued successfully!")
                # DON'T RETURN - fall through to check if rebase is complete and push
            elif "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                print("\n🔄 New conflicts appeared, looping back...")
                return _main_logic(repo_path)
            else:
                print("⚠️  Rebase failed:")
                print(result.stdout)
                return
        
        # Check if rebase is actually done now
        git_dir_check = repo_path / ".git"
        still_in_rebase = (git_dir_check / "rebase-merge").exists() or (git_dir_check / "rebase-apply").exists()
        
        if not still_in_rebase:
            # Rebase completed! Fall through to continue with push
            print("\n✅ Rebase completed! Continuing with release...")
            # Don't return - let it fall through to the version/push logic below
        elif conflict_files:
            print(f"\n   ⚠️  MERGE CONFLICTS ({len(conflict_files)}):")
            for f in conflict_files:
                print(f"     - {f}")
            
            print("\nWhat would you like to do?")
            print("  1. RESOLVE - Resolve conflicts (pick OURS/THEIRS)")
            print("  2. ABORT   - Abort rebase/merge and start fresh")
            
            choice = input("\nChoice (1-2): ").strip()
            
            if choice == '1':
                # Check if resolve_conflicts.py exists
                resolver_path = Path(__file__).parent / "resolve_conflicts.py"
                
                if resolver_path.exists():
                    print("\n🔧 Launching standalone conflict resolver...")
                    subprocess.call(["python3", str(resolver_path)], cwd=repo_path)
                    
                    # Check if rebase was aborted in the resolver
                    git_dir_check = repo_path / ".git"
                    still_in_rebase = (git_dir_check / "rebase-merge").exists() or (git_dir_check / "rebase-apply").exists()
                    
                    if not still_in_rebase:
                        print("\n✓ Rebase was aborted. Exiting.")
                        return
                    
                    # Check if conflicts are resolved
                    remaining = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path, check=False)
                    remaining_files = [f for f in remaining.split('\n') if f.strip()]
                    
                    if remaining_files:
                        print(f"\n⚠️  Still have {len(remaining_files)} unresolved file(s):")
                        for f in remaining_files:
                            print(f"  - {f}")
                        print("\nResolve them manually and re-run gitship releasegit")
                        return
                    else:
                        print("\n✅ All conflicts resolved!")
                else:
                    # Fallback: simple inline resolution
                    print("\n⚠️  resolve_conflicts.py not found, using simple resolver...")
                    print("    (Place resolve_conflicts.py in same dir as releasegit.py for full features)")
                    
                    for f in conflict_files:
                        print(f"\n📁 File: {f}")
                        print("  O - Keep OURS (local)")
                        print("  T - Keep THEIRS (remote/incoming)")
                        
                        file_choice = input("Choice (O/T): ").strip().upper()
                        
                        if file_choice == 'O':
                            run_git(["checkout", "--ours", f], cwd=repo_path)
                            run_git(["add", f], cwd=repo_path)
                            print(f"  ✓ Kept OURS")
                        elif file_choice == 'T':
                            run_git(["checkout", "--theirs", f], cwd=repo_path)
                            run_git(["add", f], cwd=repo_path)
                            print(f"  ✓ Kept THEIRS")
                        else:
                            print(f"  ⚠️ Skipped {f}")
                            continue
                
                # Continue rebase with atomic stashing
                print("\n🔄 Continuing rebase...")
                result = atomic_stash_and_run(repo_path, ["rebase", "--continue"], "rebase continue")
                
                if result.returncode == 0:
                    print("✓ Rebase continued successfully!")
                    print("\nNow re-run releasegit to complete the release.")
                    return
                else:
                    # Check if new conflicts appeared
                    if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                        print("\n🚨 NEW CONFLICTS appeared during rebase!")
                        print("   The rebase progressed but hit conflicts in another commit.")
                        print("\n   Re-running conflict detection...")
                        # LOOP BACK to detect and resolve the new conflicts
                        return _main_logic(repo_path)
                    else:
                        print("⚠️ Rebase continue had issues:")
                        print(result.stdout)
                        print(result.stderr)
                        return
                
            else:  # Abort
                if in_rebase:
                    run_git(["rebase", "--abort"], cwd=repo_path)
                else:
                    run_git(["merge", "--abort"], cwd=repo_path)
                print("✓ Aborted. Starting fresh...")
                # Fall through to normal flow
                
        elif uncommitted_files:
            # Uncommitted changes blocking rebase (like translation files)
            print(f"\n   ⚠️  UNCOMMITTED CHANGES blocking rebase ({len(uncommitted_files)}):")
            for f in uncommitted_files:
                print(f"     - {f}")
            
            print("\n   These must be stashed or committed before rebase can continue.")
            print("\nWhat would you like to do?")
            print("  1. STASH & CONTINUE - Stash changes and continue rebase")
            print("  2. ABORT            - Abort rebase and start fresh")
            
            choice = input("\nChoice (1-2): ").strip()
            
            if choice == '1':
                # Stash and continue atomically
                print("\n🔒 Stashing uncommitted changes...")
                result = atomic_stash_and_run(repo_path, ["rebase", "--continue"], "rebase continue")
                
                if result.returncode == 0:
                    print("✓ Rebase continued successfully!")
                    print("\nNow re-run releasegit to complete the release.")
                    return
                else:
                    # Check if it failed due to NEW conflicts
                    if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                        print("\n🚨 NEW CONFLICTS appeared during rebase!")
                        print("   The rebase progressed but hit conflicts in another commit.")
                        print("\n   Re-running conflict detection...")
                        # DO NOT RETURN - LOOP BACK to the start to detect and resolve new conflicts
                        # Recursive call to handle the new conflict state
                        return _main_logic(repo_path)
                    else:
                        print("⚠️ Rebase continue failed:")
                        print(result.stdout)
                        print(result.stderr)
                        return
            else:
                if in_rebase:
                    run_git(["rebase", "--abort"], cwd=repo_path)
                else:
                    run_git(["merge", "--abort"], cwd=repo_path)
                print("✓ Aborted. Starting fresh...")
                
        else:
            # No conflicts AND no uncommitted changes - ready to continue
            print("\n✓ Ready to continue rebase...")
            result = atomic_stash_and_run(repo_path, ["rebase", "--continue"], "rebase continue")
            
            if result.returncode == 0:
                print("✓ Rebase continued successfully!")
                print("\nNow re-run releasegit to complete the release.")
                return
            else:
                # Check if conflicts appeared
                if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                    print("\n🚨 NEW CONFLICTS appeared during rebase!")
                    print("   The rebase progressed but hit conflicts in another commit.")
                    print("\n   Re-running conflict detection...")
                    # LOOP BACK
                    return _main_logic(repo_path)
                else:
                    print("⚠️ Rebase continue failed:")
                    print(result.stdout)
                    print(result.stderr)
                    print("\nResolve issues manually and re-run.")
                    return
    
    current_ver = get_current_version(repo_path)
    last_tag_full = get_last_tag(repo_path)
    last_ver = last_tag_full.lstrip('v') if last_tag_full else "0.0.0"
    unpushed = get_unpushed_commits(repo_path)
    
    # STATE DETECTION
    
    # Case 0: Tag exists remotely but commits not pushed (Orphaned Tag)
    if current_ver == last_ver and unpushed > 0 and check_remote_tag(repo_path, f"v{current_ver}"):
        print(f"🚨 ORPHANED TAG DETECTED: v{current_ver}")
        print(f"   Remote has tag v{current_ver}, but you have {unpushed} unpushed commits")
        print(f"   For PyPI to work, the tag MUST point to commits that exist on remote.")
        print(f"\n   FIX REQUIRED:")
        print(f"   1. Delete orphaned tag (local + remote)")
        print(f"   2. Push {unpushed} commits")
        print(f"   3. Recreate tag pointing to pushed commits")
        
        print("\nWhat would you like to do?")
        print(f"  1. AUTO-FIX:  Delete tag, push commits, recreate tag (recommended)")
        print(f"  2. ABORT:     Exit and handle manually")
        
        choice = input("\nChoice (1-2): ").strip()
        
        if choice == '1':
            tag = f"v{current_ver}"
            
            # Step 1: Delete tag (remote then local)
            print(f"\n🗑️  Step 1/3: Deleting orphaned tag {tag}...")
            try:
                run_git(["push", "origin", f":refs/tags/{tag}"], cwd=repo_path, check=False)
                print(f"  ✓ Deleted remote tag {tag}")
            except:
                print(f"  ! Remote tag delete failed (may not exist)")
            
            try:
                run_git(["tag", "-d", tag], cwd=repo_path, check=False)
                print(f"  ✓ Deleted local tag {tag}")
            except:
                print(f"  ! Local tag delete failed (may not exist)")
            
            # Step 2: Push commits
            stashed = handle_translation_stash(repo_path)
            print(f"\n📤 Step 2/3: Pushing {unpushed} commits to origin/main...")
            try:
                result = subprocess.run(
                    ["git", "pull", "origin", "main", "--rebase"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0 and ("CONFLICT" in result.stdout or "CONFLICT" in result.stderr):
                    print("\n⚠️  MERGE CONFLICTS during rebase!")
                    print("  You're in the middle of fixing an orphaned tag.")
                    print("  Please resolve conflicts manually, then re-run gitship.")
                    print("\n  Quick commands:")
                    print("    - Resolve conflicts in files")
                    print("    - git add <resolved-files>")
                    print("    - git rebase --continue")
                    print("    - Re-run: gitship releasegit")
                    if stashed:
                        restore_translation_stash(repo_path)
                    sys.exit(1)
                
                run_git(["push", "origin", "main"], cwd=repo_path)
                print("  ✓ Commits pushed successfully!")
            except SystemExit:
                print("\n⚠️  Commit push failed. Cannot recreate tag safely.")
                if stashed:
                    restore_translation_stash(repo_path)
                sys.exit(1)
            
            # Step 3: Recreate tag
            print(f"\n🏷️  Step 3/3: Recreating tag {tag}...")
            try:
                run_git(["tag", "-a", tag, "-m", f"Release {tag}"], cwd=repo_path)
                run_git(["push", "origin", tag], cwd=repo_path)
                print(f"  ✓ Tag {tag} created and pushed!")
                print("\n🎉 Orphaned tag fixed! PyPI should work now.")
            except SystemExit:
                print(f"\n⚠️  Tag creation/push failed.")
                sys.exit(1)
            
            if stashed:
                restore_translation_stash(repo_path)
            return
                
        else:
            print("Exiting. Handle the orphaned tag manually.")
            return
    
    # Case 1: Version bumped but not tagged (In Progress)
    # Case 1: Version bumped but not tagged (In Progress)
    if current_ver != last_ver and current_ver > last_ver:
        actual_git_tag = run_git(["describe", "--tags", "--abbrev=0"], cwd=repo_path, check=False)
        print(f"⚠️  RELEASE IN PROGRESS DETECTED")
        print(f"   TOML Version  : {current_ver}")
        print(f"   Latest Git Tag: {actual_git_tag or 'none'}")
        print(f"   PyPI Published: {last_tag_full or 'none'}")
        if actual_git_tag and actual_git_tag != last_tag_full:
            print(f"   ⚠️  Git tag {actual_git_tag} is NOT on PyPI — publish workflow may have failed")
        print(f"   Local Changes : {'Yes' if is_dirty(repo_path) else 'No'}")
        
        # Check if changelog exists for current version
        changelog_exists = bool(extract_changelog_section(repo_path, current_ver))
        
        print("\nWhat would you like to do?")
        if changelog_exists:
            print(f"  1. RESUME:  Commit, Tag & Push {current_ver} (Use current changelog)")
            print(f"  2. REFRESH: Regenerate Changelog & Release {current_ver} (If you added more commits)")
            print(f"  3. ABORT:   Revert TOML to {last_ver} and exit")
            print(f"  4. RESET:   Reset version to match latest tag")
            choice = input("\nChoice (1-4): ").strip()
        else:
            print(f"  1. REFRESH: Regenerate Changelog & Release {current_ver}")
            print(f"  2. ABORT:   Revert TOML to {last_ver} and exit")
            print(f"  3. RESET:   Reset version to match latest tag")
            choice = input("\nChoice (1-3): ").strip()
            # Remap choices
            if choice == '1': choice = '2'
            elif choice == '2': choice = '3'
            elif choice == '3': choice = '4'
        
        if choice == '1':
            # -- Smart resume: check tag + PyPI state before blindly calling perform_git_release
            current_branch = get_current_branch(repo_path)
            resume_tag = _build_tag_name(current_ver, current_branch)

            # Force fetch so ls-remote sees the actual remote state
            run_git(["fetch", "--tags", "origin"], cwd=repo_path, check=False)
            tag_on_remote = check_remote_tag(repo_path, resume_tag)

            from . import pypi as _pypi_mod
            _pkg_name_resume = _pypi_mod.read_package_name(repo_path)
            on_pypi = check_pypi_version_exists(_pkg_name_resume, current_ver) if _pkg_name_resume else False


            if tag_on_remote and on_pypi:
                # Fully released - nothing to do
                print(f"\n✅ {resume_tag} is already on remote AND on PyPI. Nothing to resume.")
                print("   Use option 4 (RESET) to bump to a new version.")
                return

            elif tag_on_remote and not on_pypi:
                # Tag pushed but PyPI publish failed - offer retag or force republish
                print(f"\n⚠️  Tag {resume_tag} exists on remote but version {current_ver} is NOT on PyPI.")
                print("   The publish workflow likely failed after tagging.")
                print()
                print("  [1] Re-push same tag  (triggers CI/CD again if you fixed the workflow)")
                print("  [2] Delete tag + retag (delete remote tag, recreate, re-push — same version)")
                print("  [3] Cancel")
                sub = input("\nChoice: ").strip()

                if sub == '1':
                    # GH Actions triggers on release event not tag push
                    # Must delete + recreate the GitHub release to re-trigger
                    import shutil as _shutil
                    if _shutil.which("gh"):
                        print(f"  Deleting GitHub release {resume_tag}...")
                        subprocess.run(["gh", "release", "delete", resume_tag, "-y"],
                                      cwd=repo_path, check=False, capture_output=True)
                        print(f"  Recreating GitHub release {resume_tag}...")
                        subprocess.run(["gh", "release", "create", resume_tag,
                                       "--title", f"Release {resume_tag}",
                                       "--generate-notes"],
                                      cwd=repo_path, check=False)
                        print(f"  ✓ Done — publish workflow should re-trigger now")
                    else:
                        print(f"  ⚠️  gh CLI not found.")
                        print(f"  Go to GitHub → Releases → delete and recreate {resume_tag}")
                elif sub == '2':
                    import shutil as _shutil
                    print(f"\n  Deleting GitHub release {resume_tag}...")
                    if _shutil.which("gh"):
                        subprocess.run(["gh", "release", "delete", resume_tag, "-y"],
                                      cwd=repo_path, check=False, capture_output=True)
                    print(f"  Deleting remote tag {resume_tag}...")
                    run_git(["push", "origin", f":refs/tags/{resume_tag}"], cwd=repo_path, check=False)
                    run_git(["tag", "-d", resume_tag], cwd=repo_path, check=False)
                    print(f"  ✓ Cleaned. Retagging and creating fresh release...")
                    perform_git_release(repo_path, current_ver)
                return

            else:
                # Tag not on remote yet - normal resume
                perform_git_release(repo_path, current_ver)
            return
            
        elif choice == '2':
            # Delete the CURRENT version tag (if it exists), not the last one
            tag = _build_tag_name(current_ver, get_current_branch(repo_path))
            print(f"\n🔄 Resetting release state for {tag}...")

            # Check if current version tag exists and delete it
            result = run_git(["tag", "-l", tag], cwd=repo_path, check=False)
            if result.strip():
                # 1. Delete remote tag
                run_git(["push", "origin", f":refs/tags/{tag}"], cwd=repo_path, check=False)
                print(f"  ✓ Deleted remote tag {tag}")
                
                # 2. Delete local tag
                run_git(["tag", "-d", tag], cwd=repo_path, check=False)
                print(f"  ✓ Deleted local tag {tag}")
            
            # 3. Use LAST tag as the base
            prev_tag = last_tag_full
            
            print("\nRegenerating changelog from git history...")
            # CORRECTLY UNPACK THE TUPLE
            draft, suggested_title = get_smart_changelog(repo_path, prev_tag, current_ver)
            
            # 4. USE THE EDITOR, DON'T SKIP IT
            from . import pypi
            pkg_name = pypi.read_package_name(repo_path)
            changelog_notes, github_notes, release_title = edit_notes(current_ver, draft, suggested_title, pkg_name=pkg_name)
            
            write_changelog(repo_path, changelog_notes, current_ver)
            perform_git_release(repo_path, current_ver, release_title, github_notes)
            return
            
        elif choice == '3':
            print(f"Reverting pyproject.toml to {last_ver}...")
            toml = repo_path / "pyproject.toml"
            content = toml.read_text()
            toml.write_text(re.sub(r'^version\s*=\s*".*?"', f'version = "{last_ver}"', content, count=1, flags=re.MULTILINE))
            print("Done. Exiting.")
            return
            
        elif choice == '4':
            reset_version_to_tag(repo_path)
            return

        else:
            print("Invalid choice.")
            return
    # Case 1.4: Tag exists locally but not on remote
    if last_tag_full:
        tag_on_remote = check_remote_tag(repo_path, last_tag_full)
        
        if not tag_on_remote:
            print(f"\n⚠️  UNPUSHED TAG DETECTED!")
            print(f"   Tag {last_tag_full} exists locally but NOT on remote")
            print(f"   This will cause GitHub release creation to fail.")
            
            push_now = input(f"\nPush tag to remote now? (y/n): ").strip().lower()
            
            if push_now == 'y':
                print(f"\n🚀 Pushing {last_tag_full} to remote...")
                try:
                    run_git(["push", "origin", last_tag_full], cwd=repo_path)
                    print(f"✓ Tag pushed successfully")
                except:
                    print(f"❌ Failed to push tag")
                    return
            else:
                print("Skipped. You'll need to push manually before creating releases.")
        # Case 1.4: Incomplete release - tag/release exists but code changes uncommitted OR commits ahead
    if current_ver == last_ver and last_tag_full:
        # Check if there are uncommitted code changes (excluding translations)
        status_output = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
        code_changes = []
        
        for line in status_output.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Split on whitespace - format is: "STATUS filename"
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                status_code, filename = parts
                # Exclude translations and changelog
                if '/locale/' not in filename and 'CHANGELOG.md' not in filename:
                    code_changes.append(filename)
        
        # Check if commits are ahead of the tag
        commits_ahead = 0
        try:
            res = run_git(["rev-list", "--count", f"{last_tag_full}..HEAD"], cwd=repo_path, check=False)
            commits_ahead = int(res) if res.strip() else 0
        except:
            pass

        if (code_changes or commits_ahead > 0) and check_remote_tag(repo_path, last_tag_full):
            print(f"\n🚨 INCOMPLETE RELEASE DETECTED: {last_tag_full}")
            print(f"   Tag exists on remote, but the release is not synced with HEAD.")
            
            if commits_ahead > 0:
                print(f"   ⚠️  You are {Colors.YELLOW}{commits_ahead} commit(s) ahead{Colors.RESET} of tag {last_tag_full}")
            if code_changes:
                print(f"   ⚠️  You have {Colors.YELLOW}{len(code_changes)} uncommitted file(s){Colors.RESET}")
            
            print(f"   These changes should be part of {last_tag_full}!")
            
            if code_changes:
                print(f"\n   Uncommitted Files:")
                for f in code_changes[:10]:
                    print(f"     - {f}")
                if len(code_changes) > 10:
                    print(f"     ... and {len(code_changes)-10} more")
            
            # Check if THIS VERSION already on PyPI
            from . import pypi
            package_name = pypi.read_package_name(repo_path)
            on_pypi = False
            if package_name:
                # Check if this specific version exists
                try:
                    import requests
                    # Get the version without 'v' prefix
                    check_ver = current_ver.lstrip('v') if current_ver.startswith('v') else current_ver
                    # Also try the tag format
                    tag_ver = last_tag_full.lstrip('v') if last_tag_full and last_tag_full.startswith('v') else last_tag_full
                    
                    resp = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        releases = data.get('releases', {})
                        # Check both current_ver and tag format
                        on_pypi = check_ver in releases or tag_ver in releases
                        if on_pypi:
                            print(f"[DEBUG] Found {check_ver} or {tag_ver} in PyPI releases: {list(releases.keys())[-5:]}")
                except Exception as e:
                    print(f"[DEBUG] PyPI check failed: {e}")
                    on_pypi = False
            
            print("\nWhat would you like to do?")
            if on_pypi:
                print(f"  1. NEW RELEASE: Bump to next version (REQUIRED)")
                print(f"  2. EXIT")
                choice = input("\nChoice (1-2): ").strip()
                if choice == '1':
                    choice = '2'  # Map to NEW RELEASE option
                elif choice == '2':
                    choice = '3'  # Map to EXIT
            else:
                print(f"  1. FIX IT: Delete release/tag, commit changes, recreate {last_tag_full}")
                print(f"  2. NEW RELEASE: Bump to next version and release these as new")
                print(f"  3. EXIT")
                choice = input("\nChoice (1-3): ").strip()
            
            if choice == '1':
                tag = last_tag_full
                
                # Step 1: Delete GitHub release
                print(f"\n🗑️  Step 1/5: Deleting incomplete GitHub release...")
                if shutil.which("gh"):
                    subprocess.run(["gh", "release", "delete", tag, "-y"], cwd=repo_path, check=False)
                    print(f"  ✓ Deleted release {tag}")
                
                # Step 2: Delete remote tag
                print(f"\n🗑️  Step 2/5: Deleting remote tag...")
                run_git(["push", "origin", f":refs/tags/{tag}"], cwd=repo_path, check=False)
                print(f"  ✓ Deleted remote tag")
                
                # Step 3: Delete local tag
                print(f"\n🗑️  Step 3/5: Deleting local tag...")
                run_git(["tag", "-d", tag], cwd=repo_path, check=False)
                print(f"  ✓ Deleted local tag")
                
                # Step 4: Commit changes (excluding translations)
                print(f"\n📝 Step 4/6: Committing code changes...")
                
                # Ensure publish.yml is included if it was just created/staged
                if (repo_path / ".github" / "workflows" / "publish.yml").exists():
                    run_git(["add", ".github/workflows/publish.yml"], cwd=repo_path)

                for f in code_changes:
                    run_git(["add", f], cwd=repo_path)
                
                # Only commit if there are changes
                status = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
                if status.strip():
                    run_git(["commit", "-m", f"feat: complete {tag} release with all code changes"], cwd=repo_path)
                    print(f"  ✓ Committed {len(code_changes)} files")
                else:
                    print(f"  ✓ No changes to commit")
                
                # Step 5: Update CHANGELOG (will happen when user writes notes)
                print(f"\n📝 Step 5/6: Checking CHANGELOG.md...")
                
                # Get notes from user (they already wrote them above)
                # We need to get them again to put in changelog
                notes_for_changelog = extract_changelog_section(repo_path, current_ver)
                
                if not notes_for_changelog:
                    # User will write notes next, so we'll update changelog then
                    print("  ! Will update changelog after getting release notes")
                else:
                    # Changelog exists - ask if user wants to regenerate
                    print(f"  ✓ CHANGELOG.md entry exists for {current_ver}")
                    regen = input("    Regenerate changelog from git history? (y/n): ").strip().lower()
                    
                    if regen == 'y':
                        # Get previous tag
                        prev_tag = get_last_tag(repo_path)
                        
                        print("\n📊 Reviewing changes before regenerating changelog...")
                        if not show_review_before_changelog(repo_path, prev_tag or "HEAD~10", "HEAD"):
                            print("Skipping changelog regeneration.")
                        else:
                            print("\n🔄 Regenerating changelog from git history...")
                            draft, suggested_title = get_smart_changelog(repo_path, prev_tag, current_ver)
                            
                            from . import pypi
                            pkg_name = pypi.read_package_name(repo_path) or repo_path.name
                            # Only suggest Initial Release if there are no previous tags
                            smart_suffix = "Initial Release" if not prev_tag else suggested_title
                            
                            changelog_notes, github_notes, release_title = edit_notes(current_ver, draft, smart_suffix, pkg_name=pkg_name)
                        write_changelog(repo_path, changelog_notes, current_ver)
                        notes_for_changelog = final_notes  # Update for later use
                    
                    # Commit changelog if changed
                    run_git(["add", "CHANGELOG.md"], cwd=repo_path)
                    status = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
                    if "CHANGELOG.md" in status:
                        run_git(["commit", "-m", f"docs: Update CHANGELOG for {current_ver}"], cwd=repo_path)
                        print(f"  ✓ CHANGELOG.md updated and committed")
                    else:
                        print(f"  ✓ CHANGELOG.md already committed")
                
                # Step 6: Recreate and push
                print(f"\n🚀 Step 6/6: Recreating {tag}...")
                run_git(["push", "origin", "main"], cwd=repo_path)
                run_git(["tag", "-a", tag, "-m", f"Release {tag}"], cwd=repo_path)
                run_git(["push", "origin", tag], cwd=repo_path)
                print(f"  ✓ Tag {tag} recreated and pushed")
                
                # Give GitHub API time to process the tag
                import time
                print("  ⏳ Waiting for GitHub to process tag...")
                time.sleep(3)
                
                # Create GH release
                if shutil.which("gh"):
                    print(f"\n📝 Creating GitHub release...")
                    notes = extract_changelog_section(repo_path, current_ver)
                    
                    if not notes:
                        print("   ! No changelog found. Opening editor...")
                        # FIX: Provide default title and unpack tuple
                        default_suffix = "Release"
                        changelog_notes, github_notes, user_title = edit_notes(current_ver, "", default_suffix, pkg_name=repo_path.name)
                        
                        # NOW update changelog with the notes user just wrote
                        if notes:
                            print("\n📝 Updating CHANGELOG.md with release notes...")
                            write_changelog(repo_path, changelog_notes, current_ver)
                            run_git(["add", "CHANGELOG.md"], cwd=repo_path)
                            run_git(["commit", "--amend", "--no-edit"], cwd=repo_path)  # Amend previous commit
                            run_git(["push", "--force-with-lease"], cwd=repo_path)  # Force push (safe)
                            print("  ✓ CHANGELOG.md updated")
                    else:
                        # If notes existed, we still need a title. Default to generic if not extracted.
                        # Extract actual title from changelog first line
                        lines = notes.strip().split('\n')
                        user_title = lines[0] if lines and not lines[0].startswith('**') else f"Release {current_ver}"
                        print(f"[DEBUG] Extracted title: {user_title}")

                    if notes:
                        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tf:
                            tf.write(notes)
                            notes_file = tf.name
                        
                        try:
                            # FIX: Enforce "Package vVersion - Title" format
                            from . import pypi
                            pkg_name = pypi.read_package_name(repo_path) or repo_path.name
                            final_gh_title = f"{pkg_name} {tag} - {user_title}"
                            
                            result = subprocess.run(
                                ["gh", "release", "create", tag, "-F", notes_file, "-t", final_gh_title, "--draft"], 
                                cwd=repo_path,
                                capture_output=False,  # Show output to user!
                                check=True
                            )
                            print(f"✅ Release {tag} fixed and published!")
                            
                            # Trigger PyPI publishing flow
                            username = run_git(["config", "user.name"], cwd=repo_path, check=False) or "your-username"
                            pypi.handle_pypi_publishing(
                                repo_path=repo_path,
                                version=tag,
                                changelog=notes,
                                username=username
                            )
                            
                        except subprocess.CalledProcessError as e:
                            print(f"❌ Failed to create release: {e}")
                        finally:
                            if os.path.exists(notes_file):
                                os.unlink(notes_file)
                    else:
                        print("   Skipped release creation (no notes)")
                else:
                    print("   ⚠️  GitHub CLI not found, skipping release creation")
                
                return
            
            elif choice == '2':
                # Fall through to bump logic
                pass
                
            elif choice == '3':
                sys.exit(0)

    # Case 1.5: Release exists (draft or full) but publish may have failed
    # Covers: draft release, full release not on PyPI, workflow still running
    if current_ver == last_ver and last_tag_full and shutil.which("gh"):

        from . import pypi as _pypi
        _pkg_name = _pypi.read_package_name(repo_path) or ""

        # Gather full state in one pass
        gh_release   = get_gh_release_info(repo_path, last_tag_full)
        tag_on_remote = check_remote_tag(repo_path, last_tag_full)
        on_pypi      = check_pypi_version_exists(_pkg_name, last_tag_full) if _pkg_name else False
        wf_running, wf_status = check_workflow_running(repo_path, last_tag_full)

        # ── Push local tag that never made it to remote ───────────────────────
        if not tag_on_remote:
            print(f"\n⚠️  Tag {last_tag_full} exists locally but NOT on remote.")
            push_now = input("   Push it now? (y/n): ").strip().lower()
            if push_now == 'y':
                try:
                    run_git(["push", "origin", last_tag_full], cwd=repo_path)
                    tag_on_remote = True
                    print(f"  ✓ Tag pushed")
                except Exception:
                    print("  ❌ Push failed — cannot continue")
                    return
            else:
                print("  Skipped — cannot create release without remote tag.")
                return

        # ── SCENARIO A: Draft release exists ─────────────────────────────────
        if gh_release["exists"] and gh_release["is_draft"]:
            print(f"\n📋 DRAFT RELEASE FOUND: {last_tag_full}")
            print(f"   Title: {gh_release['title']}")
            print(f"   URL:   {gh_release['url']}")
            if on_pypi:
                print(f"   ✅ Already on PyPI — you may just need to publish the draft.")
            else:
                print(f"   ⏳ Not yet on PyPI.")
            if wf_running:
                print(f"   ⚙️  Publish workflow is RUNNING — {wf_status}")

            print("\nWhat would you like to do?")
            print(f"  1. ✅ PUBLISH DRAFT as-is (trigger workflow by publishing on GitHub)")
            print(f"  2. ✏️  EDIT NOTES then republish (reuse draft text as starting point)")
            print(f"  3. 🆕 START FRESH (delete draft+tag, full re-release flow)")
            print(f"  4. 🚪 EXIT")
            choice = input("\nChoice (1-4): ").strip()

            if choice == '1':
                base_url = get_repo_url(repo_path)
                print(f"\n→ Go publish the draft at: {base_url}/releases/tag/{last_tag_full}")
                print(f"  Once published, the workflow will trigger and push to PyPI.")
                return

            elif choice == '2':
                # Pre-load draft body into editor
                saved_notes = gh_release["body"]
                saved_title = gh_release["title"] or f"Release {last_tag_full}"
                print(f"\n✏️  Opening editor with existing draft notes...")
                changelog_notes, github_notes, release_title = edit_notes(
                    current_ver, saved_notes, saved_title, pkg_name=_pkg_name or repo_path.name
                )
                # Delete old draft and recreate with new notes
                subprocess.run(["gh", "release", "delete", last_tag_full, "-y"],
                               cwd=repo_path, check=False)
                with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False) as tf:
                    tf.write(github_notes)
                    notes_file = tf.name
                try:
                    subprocess.run(
                        ["gh", "release", "create", last_tag_full,
                         "-F", notes_file, "-t", release_title,
                         "--draft", "--target", "main"],
                        cwd=repo_path, check=True
                    )
                    base_url = get_repo_url(repo_path)
                    print(f"\n✅ Updated draft: {base_url}/releases/tag/{last_tag_full}")
                    write_changelog(repo_path, changelog_notes, current_ver)
                except subprocess.CalledProcessError:
                    print("❌ Failed to recreate draft.")
                finally:
                    if os.path.exists(notes_file): os.unlink(notes_file)
                return

            elif choice == '3':
                # Fall through to FAILED PUBLISH cleanup below (shared logic)
                gh_release["is_draft"] = False  # treat as full release for cleanup
            else:
                sys.exit(0)

        # ── SCENARIO B: Full (non-draft) release exists but NOT on PyPI ───────
        if gh_release["exists"] and not gh_release["is_draft"] and not on_pypi:

            if wf_running:
                print(f"\n⚙️  PUBLISH WORKFLOW IS STILL RUNNING for {last_tag_full}")
                print(f"   Status: {wf_status}")
                print(f"   Wait for it to finish before taking action.")
                print(f"\n  1. Check again (refresh)")
                print(f"  2. Proceed anyway (dangerous — may double-publish)")
                print(f"  3. EXIT")
                c = input("\nChoice (1-3): ").strip()
                if c == '1':
                    return _main_logic(repo_path)
                elif c != '2':
                    return

            # Safe to act: full release exists, workflow done/absent, not on PyPI
            print(f"\n🚨 FAILED PUBLISH DETECTED: {last_tag_full}")
            print(f"   GitHub Release:  ✅ exists (published, not draft)")
            print(f"   PyPI:            ❌ version not found")
            print(f"   Workflow:        {'⚙️  running' if wf_running else '✅ not running'}")
            print(f"\n   The release was published but the PyPI workflow failed or")
            print(f"   never triggered. The release notes are saved below.")
            print(f"\n   Saved title: {gh_release['title']}")
            body_preview = (gh_release['body'] or '')[:300].strip()
            if body_preview:
                print(f"   Notes preview:\n{Colors.DIM}")
                for line in body_preview.splitlines()[:8]:
                    print(f"     {line}")
                print(f"{Colors.RESET}")

            print("\nWhat would you like to do?")
            print(f"  1. 🔁 RE-RELEASE same version (delete release+tag, reuse saved notes, full flow)")
            print(f"  2. ✏️  RE-RELEASE with edited notes (same but open editor first)")
            print(f"  3. ⏭️  BUMP VERSION instead (treat this as done, start next release)")
            print(f"  4. 🚪 EXIT")
            choice = input("\nChoice (1-4): ").strip()

            if choice in ('1', '2'):
                # Save notes from the full release before deleting
                saved_notes = gh_release["body"] or extract_changelog_section(repo_path, current_ver) or ""
                saved_title = gh_release["title"] or f"Release {last_tag_full}"
                tag = last_tag_full

                print(f"\n🗑️  Deleting failed release and tag...")
                subprocess.run(["gh", "release", "delete", tag, "-y"],
                               cwd=repo_path, check=False)
                print(f"  ✓ Deleted GitHub release {tag}")
                run_git(["push", "origin", f":refs/tags/{tag}"], cwd=repo_path, check=False)
                print(f"  ✓ Deleted remote tag {tag}")
                run_git(["tag", "-d", tag], cwd=repo_path, check=False)
                print(f"  ✓ Deleted local tag {tag}")

                if choice == '2':
                    print(f"\n✏️  Opening editor with saved release notes...")
                    prev_tag = get_last_tag(repo_path)
                    changelog_notes, github_notes, release_title = edit_notes(
                        current_ver, saved_notes, saved_title,
                        pkg_name=_pkg_name or repo_path.name
                    )
                else:
                    # Reuse notes exactly as-is
                    github_notes   = saved_notes
                    changelog_notes = saved_notes
                    release_title  = saved_title
                    print(f"  ✓ Reusing saved notes: \"{release_title}\"")

                write_changelog(repo_path, changelog_notes, current_ver)
                perform_git_release(repo_path, current_ver, release_title, github_notes)
                return

            elif choice == '3':
                # Fall through to version bump
                pass
            else:
                sys.exit(0)

        # ── SCENARIO C: No GH release at all ─────────────────────────────────
        if not gh_release["exists"] and tag_on_remote:
            print(f"\n⚠️  Tag {last_tag_full} is on remote but has NO GitHub release.")
            print("\nWhat would you like to do?")
            print(f"  1. 📝 CREATE draft release (auto-extract notes from CHANGELOG)")
            print(f"  2. 🔄 DELETE TAG & start full re-release flow")
            print(f"  3. ⏭️  BUMP VERSION (start next release)")
            print(f"  4. 🚪 EXIT")
            choice = input("\nChoice (1-4): ").strip()

            if choice == '1':
                notes = extract_changelog_section(repo_path, current_ver)
                if not notes:
                    is_first = current_ver.startswith('0.') or current_ver == '1.0.0'
                    changelog_notes, notes, release_title = edit_notes(
                        current_ver, "", "Initial Release" if is_first else "Release",
                        pkg_name=_pkg_name or repo_path.name
                    )
                else:
                    release_title = f"Release {last_tag_full}"
                with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False) as tf:
                    tf.write(notes)
                    notes_file = tf.name
                try:
                    subprocess.run(
                        ["gh", "release", "create", last_tag_full,
                         "-F", notes_file, "-t", release_title,
                         "--draft", "--target", "main"],
                        cwd=repo_path, check=True
                    )
                    base_url = get_repo_url(repo_path)
                    print(f"\n✅ Draft created: {base_url}/releases/tag/{last_tag_full}")
                    username = run_git(["config", "user.name"], cwd=repo_path, check=False) or "your-username"
                    from . import pypi
                    pypi.handle_pypi_publishing(
                        repo_path=repo_path, version=last_tag_full,
                        changelog=notes, username=username
                    )
                except subprocess.CalledProcessError:
                    print("❌ Failed to create draft. Check gh auth.")
                finally:
                    if os.path.exists(notes_file): os.unlink(notes_file)
                print("\n🔄 Refreshing state...")
                return _main_logic(repo_path)

            elif choice == '2':
                tag = last_tag_full
                run_git(["push", "origin", f":refs/tags/{tag}"], cwd=repo_path, check=False)
                print(f"  ✓ Deleted remote tag {tag}")
                run_git(["tag", "-d", tag], cwd=repo_path, check=False)
                print(f"  ✓ Deleted local tag {tag}")
                prev_tag = get_last_tag(repo_path)
                print("\n📊 Reviewing changes before regenerating changelog...")
                if not show_review_before_changelog(repo_path, prev_tag or get_first_commit_ref(repo_path), "HEAD"):
                    print("Release cancelled.")
                    return
                draft, suggested_title = get_smart_changelog(repo_path, prev_tag, current_ver)
                changelog_notes, github_notes, release_title = edit_notes(
                    current_ver, draft, suggested_title, pkg_name=_pkg_name or repo_path.name
                )
                write_changelog(repo_path, changelog_notes, current_ver)
                perform_git_release(repo_path, current_ver, release_title, github_notes)
                return

            elif choice == '3':
                pass  # fall through to bump
            elif choice.lower() == 'reset':
                reset_version_to_tag(repo_path)
                return
            else:
                sys.exit(0)

    # Case 2: Clean Slate (Normal Flow)
    print(f"\nCurrent Version: {current_ver}")

    # Detect scheme: CVE (YYYY.CVENUM[.patch]) or semver
    _cve_match = re.match(r'^(20\d\d)\.(\d{5,})(?:\.(\d+))?$', current_ver)
    current_branch = get_current_branch(repo_path)
    custom_tag = ""  # may be set below

    if _cve_match:
        # ── CVE version scheme ─────────────────────────────────────────────────
        year      = _cve_match.group(1)
        cve_num   = _cve_match.group(2)
        cur_patch = int(_cve_match.group(3) or 0)
        base_ver  = f"{year}.{cve_num}"
        auto_next = f"{base_ver}.{cur_patch + 1}"
        auto_tag  = _build_tag_name(auto_next, current_branch)

        print(f"  Detected CVE version scheme  (branch: {current_branch})")
        print(f"\n  [1] Increment patch suffix   {current_ver} -> {auto_next}  (tag: {auto_tag})")
        print(f"  [2] New CVE base              enter YYYY.CVENUM manually")
        print(f"  [3] Custom version + tag      full manual control")
        print(f"  [4] Cancel")
        c = input("\nChoice: ").strip()

        if c == '1':
            new_ver = auto_next
        elif c == '2':
            new_ver = input("  Version (e.g. 2026.99999 or 2026.99999.1): ").strip()
            if not new_ver:
                print("Cancelled."); return
            if not _validate_pypi_version(new_ver):
                print(f"  ⚠️  '{new_ver}' may not be a valid PyPI version. Continue anyway? [y/N]: ", end="")
                if input().strip().lower() != 'y':
                    return
        elif c == '3':
            new_ver = input("  Version (PyPI): ").strip()
            if not new_ver:
                print("Cancelled."); return
            if not _validate_pypi_version(new_ver):
                print(f"  ⚠️  '{new_ver}' may not be valid on PyPI. Continue anyway? [y/N]: ", end="")
                if input().strip().lower() != 'y':
                    return
            suggested_tag = _build_tag_name(new_ver, current_branch)
            print(f"  Suggested tag: {suggested_tag}")
            custom_input = input(f"  Tag (enter to use suggested): ").strip()
            if custom_input:
                if not _validate_git_tag(custom_input):
                    print(f"  ⚠️  '{custom_input}' contains characters invalid for git tags.")
                    if input("  Use anyway? [y/N]: ").strip().lower() != 'y':
                        return
                custom_tag = custom_input
        else:
            print("Cancelled."); return

    else:
        # ── Semver + custom option ─────────────────────────────────────────────
        parts = current_ver.split('.')
        while len(parts) < 3:
            parts.append('0')
        try:
            major, minor, patch_n = int(parts[0]), int(parts[1]), int(parts[2])
            opts = {
                '1': ('patch', f"{major}.{minor}.{patch_n+1}"),
                '2': ('minor', f"{major}.{minor+1}.0"),
                '3': ('major', f"{major+1}.0.0"),
            }
            recommendation, reasoning = _recommend_bump_type(repo_path, last_tag_full)
            print(f"\n  [1] Patch   {current_ver} -> {opts['1'][1]}")
            print(f"  [2] Minor   {current_ver} -> {opts['2'][1]}")
            print(f"  [3] Major   {current_ver} -> {opts['3'][1]}")
            print(f"  [4] Custom  enter version + optional tag manually")
            print(f"\n  💡 Recommended: {recommendation.upper()} — {reasoning}")
            c = input("\nBump type: ").strip()

            if c in opts:
                new_ver = opts[c][1]
            elif c == '4':
                new_ver = input("  Version (PyPI): ").strip()
                if not new_ver:
                    print("Cancelled."); return
                if not _validate_pypi_version(new_ver):
                    print(f"  ⚠️  '{new_ver}' may not be valid on PyPI. Continue anyway? [y/N]: ", end="")
                    if input().strip().lower() != 'y':
                        return
                suggested_tag = f"v{new_ver}"
                print(f"  Suggested tag: {suggested_tag}")
                custom_input = input(f"  Tag (enter to use suggested): ").strip()
                if custom_input:
                    if not _validate_git_tag(custom_input):
                        print(f"  ⚠️  '{custom_input}' contains characters invalid for git tags.")
                        if input("  Use anyway? [y/N]: ").strip().lower() != 'y':
                            return
                    custom_tag = custom_input
            else:
                print("Invalid choice."); return
        except ValueError:
            # Non-numeric version - just offer custom
            print(f"  Version '{current_ver}' is non-standard. Enter new version manually.")
            new_ver = input("  Version (PyPI): ").strip()
            if not new_ver:
                print("Cancelled."); return

    print(f"\n  {current_ver}  ->  {new_ver}  (tag: {custom_tag or _build_tag_name(new_ver, current_branch)})")
    
    # Update TOML immediately
    toml = repo_path / "pyproject.toml"
    content = toml.read_text()
    toml.write_text(re.sub(r'^version\s*=\s*".*?"', f'version = "{new_ver}"', content, count=1, flags=re.MULTILINE))
    print("✓ Updated pyproject.toml")
    
    # Show review before changelog
    print("\n📊 Reviewing changes before generating changelog...")
    if not show_review_before_changelog(repo_path, last_tag_full or "HEAD~10", "HEAD"):
        print("Release cancelled. Reverting pyproject.toml...")
        content = toml.read_text()
        toml.write_text(re.sub(r'^version\s*=\s*".*?"', f'version = "{current_ver}"', content, count=1, flags=re.MULTILINE))
        return
    
    # Changelog
    draft, suggested_title = get_smart_changelog(repo_path, last_tag_full, new_ver)
    # Smart suffix for first release
    # Check if on PyPI to determine if first release
    # Check if ANY version on PyPI to determine if first release
    from . import pypi as pypi_module
    pkg_name_check = pypi_module.read_package_name(repo_path)
    
    # Check if package has ANY releases on PyPI (not just this version)
    is_first_release = False
    if pkg_name_check:
        try:
            import requests
            resp = requests.get(f"https://pypi.org/pypi/{pkg_name_check}/json", timeout=5)
            if resp.status_code == 404:
                # Package doesn't exist at all on PyPI
                is_first_release = True
            elif resp.status_code == 200:
                # Package exists, check if it has any releases
                data = resp.json()
                is_first_release = len(data.get('releases', {})) == 0
        except:
            is_first_release = False
    
    smart_suffix = "Initial Release" if is_first_release else suggested_title
    changelog_notes, github_notes, release_title = edit_notes(new_ver, draft, smart_suffix, pkg_name=repo_path.name)
    write_changelog(repo_path, changelog_notes, new_ver)
    
    # Finish
    perform_git_release(repo_path, new_ver, release_title, github_notes, custom_tag=custom_tag)

if __name__ == "__main__":
    main_with_repo(Path.cwd())