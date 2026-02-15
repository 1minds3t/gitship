#!/usr/bin/env python3
"""
commit - Intelligent git commit analyzer and creator.

Analyzes changes in the working directory, categorizes them intelligently,
and helps create meaningful commit messages with proper grouping.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
import re


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Foreground colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright variants
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


class ChangeAnalyzer:
    """Analyze git changes and categorize them intelligently."""
    
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.changes = {
            'code': [],
            'translations': defaultdict(list),
            'tests': [],
            'docs': [],
            'config': [],
            'other': [],
            'renames': []
        }
        self.translation_stats = {}
    
    def run_git(self, args: List[str]) -> subprocess.CompletedProcess:
        """Run a git command."""
        return subprocess.run(
            ["git"] + args,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False
        )
    
    def analyze_changes(self) -> Dict:
        """Analyze all changes in the repository."""
        # Get all files first
        result = self.run_git(["status", "--porcelain"])
        if result.returncode != 0:
            return self.changes
        
        print(f"\n[DEBUG] Raw git status output:")
        print(result.stdout)
        print(f"[DEBUG] Parsing {len(result.stdout.strip().split(chr(10)))} lines")
        print()
        
        all_files = []
        deleted_files = []
        untracked_files = []
        git_detected_renames = []
        
        # Split lines WITHOUT stripping first (preserves leading spaces in status codes)
        for line in result.stdout.split('\n'):
            # Only strip trailing whitespace from each line
            line = line.rstrip()
            if not line:
                continue
            
            # Git status --porcelain format: "XY filename"
            # where X and Y are status codes (or spaces)
            # The filename starts at position 3 (after 2 status chars + 1 space)
            if len(line) < 4:
                continue
                
            status = line[:2]  # Keep the raw status (may include spaces)
            filepath = line[3:]  # Start after "XY " - NO strip here, we already rstripped
            
            # Clean up status for comparison (remove spaces)
            status_clean = status.strip()
            
            # Check for git-detected renames (status R or R100, etc.)
            if status.startswith('R'):
                # Format: "R  old_path -> new_path"
                if ' -> ' in filepath:
                    old_path, new_path = filepath.split(' -> ', 1)
                    git_detected_renames.append({'old': old_path.strip(), 'new': new_path.strip()})
                    # Don't add to all_files, we'll handle separately
                    continue
            
            all_files.append({'status': status, 'path': filepath})
            
            # Use status_clean for comparisons (without spaces)
            if status_clean == 'D':
                deleted_files.append(filepath)
            elif status == '??':
                untracked_files.append(filepath)
        
        print(f"[DEBUG] Total files: {len(all_files)}")
        print(f"[DEBUG] Deleted: {len(deleted_files)}, Untracked: {len(untracked_files)}")
        print(f"[DEBUG] Git-detected renames: {len(git_detected_renames)}")
        
        # First, handle git-detected renames
        for rename_info in git_detected_renames:
            content_changed = self._check_rename_content_change(
                rename_info['old'], 
                rename_info['new']
            )
            self.changes['renames'].append({
                'old': rename_info['old'],
                'new': rename_info['new'],
                'status': 'R',
                'content_changed': content_changed
            })
        
        # Then detect our own renames from deleted/untracked
        self._detect_renames(deleted_files, untracked_files)
        
        # Build set of all files involved in renames (both old and new paths)
        renamed_files = set()
        for item in self.changes['renames']:
            renamed_files.add(item['old'])
            renamed_files.add(item['new'])
        
        print(f"[DEBUG] Renamed files to exclude: {renamed_files}")
        
        # Now categorize remaining files, skipping renamed ones
        # BUT if a rename has content changes, also include it in the appropriate category
        categorized = 0
        for file_info in all_files:
            filepath = file_info['path']
            status = file_info['status']
            
            # Skip if this file is part of a rename (we'll handle renames separately)
            if filepath in renamed_files:
                print(f"[DEBUG] Skipping {filepath} (part of rename)")
                continue
            
            self._categorize_file(filepath, status)
            categorized += 1
        
        # Now add renamed files WITH content changes to their respective categories
        # But DON'T duplicate - check if already added
        already_added = set()
        for rename_item in self.changes['renames']:
            if rename_item.get('content_changed', False):
                new_path = rename_item['new']
                
                # Check if not already in the code list
                if not any(item['path'] == new_path for item in self.changes['code']):
                    # Determine category based on file extension/path
                    if new_path.endswith('.py'):
                        self.changes['code'].append({
                            'path': new_path, 
                            'status': 'R', 
                            'rename_from': rename_item['old']
                        })
                        print(f"[DEBUG] Also categorizing rename {new_path} as code (content changed)")
                        already_added.add(new_path)
        
        print(f"[DEBUG] Categorized {categorized} files")
        print()
        
        return self.changes
    
    def _detect_renames(self, deleted_files: List[str], untracked_files: List[str]):
        """Detect renamed files using deleted and untracked file lists with content similarity."""
        print(f"\n[DEBUG] Detecting renames...")
        print(f"[DEBUG] Deleted files: {deleted_files}")
        print(f"[DEBUG] Untracked files: {untracked_files}")
        
        matched_untracked = set()
        
        # Match deleted -> untracked by content similarity
        for old in deleted_files:
            best_match = None
            best_similarity = 0.0
            
            # Get old file content from git
            try:
                result = self.run_git(["show", f"HEAD:{old}"])
                if result.returncode != 0:
                    continue
                old_content = result.stdout
                old_lines = set(old_content.splitlines())
            except:
                continue
            
            # Compare with each untracked file
            for new in untracked_files:
                if new in matched_untracked:
                    continue
                
                # Skip if not in same directory or wrong extension
                if Path(old).parent != Path(new).parent:
                    continue
                if Path(old).suffix != Path(new).suffix:
                    continue
                
                # Get new file content
                try:
                    new_path = self.repo_path / new
                    with open(new_path, 'r', encoding='utf-8', errors='ignore') as f:
                        new_content = f.read()
                    new_lines = set(new_content.splitlines())
                except:
                    continue
                
                # Calculate similarity (Jaccard similarity)
                if len(old_lines) == 0 and len(new_lines) == 0:
                    similarity = 1.0
                elif len(old_lines) == 0 or len(new_lines) == 0:
                    similarity = 0.0
                else:
                    intersection = len(old_lines & new_lines)
                    union = len(old_lines | new_lines)
                    similarity = intersection / union if union > 0 else 0.0
                
                print(f"[DEBUG] Similarity {old} ↔ {new}: {similarity:.2%}")
                
                # Consider it a rename if >50% similar
                if similarity > 0.5 and similarity > best_similarity:
                    best_similarity = similarity
                    best_match = new
            
            if best_match:
                print(f"[DEBUG] ✓ RENAME DETECTED: {old} → {best_match} ({best_similarity:.2%} similar)")
                matched_untracked.add(best_match)
                
                # Check if content changed
                content_changed = best_similarity < 0.99
                
                self.changes['renames'].append({
                    'old': old,
                    'new': best_match,
                    'status': 'R',
                    'content_changed': content_changed
                })
        
        print(f"[DEBUG] Total renames detected: {len(self.changes['renames'])}")
        print()
    
    
    def _check_rename_content_change(self, old_path: str, new_path: str) -> bool:
        """Check if a renamed file has content changes."""
        try:
            # Get old file content from HEAD
            result_old = self.run_git(["show", f"HEAD:{old_path}"])
            if result_old.returncode != 0:
                return True  # Can't compare, assume changed
            
            old_content = result_old.stdout
            
            # Get new file content from working directory
            new_file = self.repo_path / new_path
            if not new_file.exists():
                return True
            
            with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                new_content = f.read()
            
            # Compare content
            return old_content != new_content
        except:
            return True  # On error, assume changed
    
    def _categorize_file(self, filepath: str, status: str):
        """Categorize a single file change."""
        path = Path(filepath)
        
        # Translation files
        if 'locale' in path.parts or filepath.endswith(('.po', '.pot', '.mo')):
            self._analyze_translation(filepath, status)
        
        # Test files
        elif 'test' in filepath.lower() or (len(path.parts) > 0 and path.parts[0] in ('tests', 'test')):
            self.changes['tests'].append({'path': filepath, 'status': status})
        
        # Documentation
        elif filepath.endswith(('.md', '.rst', '.txt')):
            self.changes['docs'].append({'path': filepath, 'status': status})
        
        # Config files
        elif filepath.endswith(('.toml', '.ini', '.cfg', '.yaml', '.yml', '.json')) or \
             filepath in ('.gitignore', '.dockerignore', 'Makefile', '.gitattributes'):
            self.changes['config'].append({'path': filepath, 'status': status})
        
        # Code files
        elif filepath.endswith(('.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.go', '.rs')):
            self.changes['code'].append({'path': filepath, 'status': status})
        
        # Other
        else:
            self.changes['other'].append({'path': filepath, 'status': status})
    
    def _analyze_translation(self, filepath: str, status: str):
        """Analyze translation file changes."""
        # Extract language code from path
        match = re.search(r'/locale/([^/]+)/', filepath)
        if not match:
            return
        
        lang_code = match.group(1)
        
        # Determine file type
        if filepath.endswith('.pot'):
            file_type = 'template'
        elif filepath.endswith('.po'):
            file_type = 'source'
        elif filepath.endswith('.mo'):
            file_type = 'compiled'
        else:
            file_type = 'other'
        
        self.changes['translations'][lang_code].append({
            'path': filepath,
            'status': status,
            'type': file_type
        })
        
        # Analyze .po file if it's modified
        if file_type == 'source' and status in ('M', 'MM'):
            self._analyze_po_file(filepath, lang_code)
    
    def _analyze_po_file(self, filepath: str, lang_code: str):
        """Analyze a .po file to extract translation statistics."""
        try:
            result = self.run_git(["diff", "HEAD", filepath])
            if result.returncode != 0:
                return
            
            diff = result.stdout
            
            # Count changes
            added_translations = len(re.findall(r'^\+msgstr "(.+)"', diff, re.MULTILINE))
            removed_empty = len(re.findall(r'^-msgstr ""', diff, re.MULTILINE))
            fuzzy_changes = len(re.findall(r'[+-]#.*fuzzy', diff, re.MULTILINE))
            
            self.translation_stats[lang_code] = {
                'added': added_translations,
                'removed_empty': removed_empty,
                'fuzzy_changes': fuzzy_changes
            }
        except Exception:
            pass
    
    def display_summary(self):
        """Display a summary of all changes."""
        print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
        print(f"{Colors.BOLD}COMMIT ANALYSIS - Changes Detected{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}")
        
        # Renames
        if self.changes['renames']:
            print(f"\n{Colors.CYAN}🔄 Renamed Files ({len(self.changes['renames'])} files):{Colors.RESET}")
            for item in self.changes['renames']:
                content_note = f"{Colors.YELLOW} (content changed){Colors.RESET}" if item.get('content_changed', False) else f"{Colors.DIM} (identical){Colors.RESET}"
                print(f"  {Colors.BLUE}📝 {item['old']} → {item['new']}{Colors.RESET}{content_note}")
        
        # Code changes
        if self.changes['code']:
            print(f"\n{Colors.GREEN}📝 Code Files ({len(self.changes['code'])} files):{Colors.RESET}")
            for item in self.changes['code']:
                status_icon = self._get_status_icon(item['status'])
                print(f"  {status_icon} {item['path']}")
        
        # Translation changes
        if self.changes['translations']:
            print(f"\n{Colors.MAGENTA}🌍 Translations ({len(self.changes['translations'])} languages):{Colors.RESET}")
            for lang, files in sorted(self.changes['translations'].items()):
                po_count = sum(1 for f in files if f['type'] == 'source')
                mo_count = sum(1 for f in files if f['type'] == 'compiled')
                
                stats_str = ""
                if lang in self.translation_stats:
                    stats = self.translation_stats[lang]
                    if stats['added'] > 0:
                        stats_str = f" {Colors.GREEN}(+{stats['added']} translations){Colors.RESET}"
                
                print(f"  🔤 {lang}: {po_count} .po, {mo_count} .mo files{stats_str}")
        
        # Tests
        if self.changes['tests']:
            print(f"\n{Colors.YELLOW}🧪 Tests ({len(self.changes['tests'])} files):{Colors.RESET}")
            for item in self.changes['tests']:
                status_icon = self._get_status_icon(item['status'])
                print(f"  {status_icon} {item['path']}")
        
        # Docs
        if self.changes['docs']:
            print(f"\n{Colors.BLUE}📚 Documentation ({len(self.changes['docs'])} files):{Colors.RESET}")
            for item in self.changes['docs']:
                status_icon = self._get_status_icon(item['status'])
                print(f"  {status_icon} {item['path']}")
        
        # Config
        if self.changes['config']:
            print(f"\n{Colors.CYAN}⚙️  Configuration ({len(self.changes['config'])} files):{Colors.RESET}")
            for item in self.changes['config']:
                status_icon = self._get_status_icon(item['status'])
                safe_path = self._safe_display_path(item['path'])
                print(f"  {status_icon} {safe_path}")
        
        # Other
        if self.changes['other']:
            print(f"\n{Colors.DIM}📦 Other ({len(self.changes['other'])} files):{Colors.RESET}")
            for item in self.changes['other']:
                status_icon = self._get_status_icon(item['status'])
                print(f"  {status_icon} {item['path']}")
        
        print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    
    def _get_status_icon(self, status: str) -> str:
        """Get an icon for a git status."""
        if 'A' in status:
            return '➕'
        elif 'M' in status:
            return '✏️ '
        elif 'D' in status:
            return '❌'
        elif '?' in status:
            return '❓'
        return '  '
    
    def _safe_display_path(self, path: str) -> str:
        """Safely display a path, handling encoding issues."""
        try:
            # Try to encode/decode to catch issues
            return path.encode('utf-8', errors='replace').decode('utf-8')
        except:
            # Fallback - replace problematic chars
            return ''.join(c if c.isprintable() else '?' for c in path)


class CommitMessageBuilder:
    """Build commit messages based on change analysis."""
    
    def __init__(self, analyzer: ChangeAnalyzer):
        self.analyzer = analyzer
    
    def suggest_commit_message(self) -> str:
        """Suggest a commit message based on changes."""
        changes = self.analyzer.changes
        
        # Build header (short summary)
        header_parts = []
        
        # Check for renames first
        if changes['renames']:
            rename_desc = ", ".join([f"{Path(r['old']).stem}→{Path(r['new']).stem}" for r in changes['renames'][:3]])
            if len(changes['renames']) > 3:
                rename_desc += f" (+{len(changes['renames'])-3} more)"
            header_parts.append(f"Rename: {rename_desc}")
        
        # Determine primary change type
        if changes['code']:
            header_parts.append(f"Update {len(changes['code'])} code files")
        
        if changes['translations']:
            header_parts.append(f"Update translations ({len(changes['translations'])} languages)")
        
        if changes['tests']:
            header_parts.append("Update tests")
        
        if changes['docs']:
            header_parts.append("Update documentation")
        
        if changes['config']:
            header_parts.append("Update configuration")
        
        if not header_parts:
            header_parts.append("Update files")
        
        header = "; ".join(header_parts)
        
        # Build detailed description
        description_lines = []
        
        # Add renames with line count changes
        if changes['renames']:
            description_lines.append("\nRenames:")
            for item in changes['renames']:
                # Calculate line changes for rename
                try:
                    result_old = self.analyzer.run_git(["show", f"HEAD:{item['old']}"])
                    old_lines_count = len(result_old.stdout.splitlines()) if result_old.returncode == 0 else 0
                    
                    new_path = self.analyzer.repo_path / item['new']
                    if new_path.exists():
                        with open(new_path, 'r', encoding='utf-8', errors='ignore') as f:
                            new_lines_count = len(f.readlines())
                    else:
                        new_lines_count = 0
                    
                    if item.get('content_changed'):
                        diff = new_lines_count - old_lines_count
                        sign = '+' if diff >= 0 else ''
                        description_lines.append(f"  • {item['old']} → {item['new']} ({sign}{diff} lines)")
                    else:
                        description_lines.append(f"  • {item['old']} → {item['new']}")
                except:
                    description_lines.append(f"  • {item['old']} → {item['new']}")
        
        # Add new files (ALL of them, no limit)
        new_files = []
        for item in changes['code']:
            if item['status'] in ('??', 'A') and 'rename_from' not in item:
                new_files.append(item)
        for item in changes['tests']:
            if item['status'] in ('??', 'A'):
                new_files.append(item)
        for item in changes['docs']:
            if item['status'] in ('??', 'A'):
                new_files.append(item)
        for item in changes['config']:
            if item['status'] in ('??', 'A'):
                new_files.append(item)
        for item in changes['other']:
            if item['status'] in ('??', 'A'):
                new_files.append(item)
        
        if new_files:
            description_lines.append("\nNew files:")
            for item in new_files:  # NO LIMIT - show ALL
                # Get line count
                try:
                    file_path = self.analyzer.repo_path / item['path']
                    if file_path.is_file():
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = len(f.readlines())
                        description_lines.append(f"  • {item['path']} ({lines} lines)")
                    else:
                        description_lines.append(f"  • {item['path']}")
                except:
                    description_lines.append(f"  • {item['path']}")
        
        # Add modified files (ALL of them, no limit)
        modified_files = []
        for item in changes['code']:
            # Status can be " M", "M ", "MM", etc - check if M is present
            if 'M' in item['status'] and 'rename_from' not in item:
                modified_files.append(item)
        for item in changes['config']:
            if 'M' in item['status']:
                modified_files.append(item)
        for item in changes['docs']:
            if 'M' in item['status']:
                modified_files.append(item)
        
        if modified_files:
            description_lines.append("\nModified:")
            for item in modified_files:  # NO LIMIT - show ALL
                try:
                    import difflib
                    # Get old content from HEAD
                    result_old = self.analyzer.run_git(["show", f"HEAD:{item['path']}"])
                    old_lines = result_old.stdout.splitlines() if result_old.returncode == 0 else []
                    
                    # Get new content from working directory
                    new_file = self.analyzer.repo_path / item['path']
                    with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                        new_lines = f.read().splitlines()
                    
                    # Calculate diff
                    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                    additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
                    deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
                    
                    # Format the output
                    if additions > 0 or deletions > 0:
                        description_lines.append(f"  • {item['path']} (+{additions}/-{deletions} lines)")
                    else:
                        description_lines.append(f"  • {item['path']}")
                except:
                    description_lines.append(f"  • {item['path']}")
        
        # Add translation summary if significant
        if changes['translations']:
            total_added = sum(
                stats.get('added', 0) 
                for stats in self.analyzer.translation_stats.values()
            )
            if total_added > 0:
                description_lines.append(f"\nTranslations: +{total_added} strings across {len(changes['translations'])} languages")
        
        # Combine header and description
        if description_lines:
            return header + '\n' + '\n'.join(description_lines)
        else:
            return header
    
    def _suggest_code_message(self) -> str:
        """Suggest a message for code changes."""
        code_files = self.analyzer.changes['code']
        
        if len(code_files) == 1:
            filepath = code_files[0]['path']
            module_name = Path(filepath).stem
            return f"Update {module_name}"
        else:
            return f"Update {len(code_files)} code files"
    
    def _suggest_translation_message(self) -> str:
        """Suggest a message for translation changes."""
        translations = self.analyzer.changes['translations']
        lang_count = len(translations)
        
        # Check if it's mostly compiled files
        all_files = [f for files in translations.values() for f in files]
        compiled_count = sum(1 for f in all_files if f['type'] == 'compiled')
        
        if compiled_count == len(all_files):
            return f"Recompile translations ({lang_count} languages)"
        
        # Check for significant additions
        total_added = sum(
            stats.get('added', 0) 
            for stats in self.analyzer.translation_stats.values()
        )
        
        if total_added > 100:
            return f"Add translations for {lang_count} languages (+{total_added} strings)"
        elif total_added > 0:
            return f"Update translations ({lang_count} languages)"
        else:
            return f"Update translation files ({lang_count} languages)"


def show_diff_menu(analyzer: ChangeAnalyzer, category: str, files: List[Dict]):
    """Show hierarchical diff viewing menu for a category."""
    print(f"\n{'=' * 80}")
    print(f"{category.upper()} CHANGES - Review Options")
    print("=" * 80)
    print(f"\nFiles to review: {len(files)}")
    print()
    print("Review options:")
    print("  1. Summary only (--shortstat)")
    print("  2. File list with stats (--stat)")
    print("  3. Full diff (--patch)")
    
    # Add special option for renames
    if files and 'old' in files[0]:
        print("  4. Stage renames properly (git rm + git add)")
        print("  5. Back to main menu")
        max_choice = 5
    else:
        print("  4. Back to main menu")
        max_choice = 4
    
    print()
    
    try:
        choice = input(f"Choose option (1-{max_choice}): ").strip()
    except KeyboardInterrupt:
        print("\n\nBack to main menu.")
        return
    
    if choice == '1':
        show_shortstat(analyzer, files)
    elif choice == '2':
        show_stat(analyzer, files)
    elif choice == '3':
        show_full_diff(analyzer, files)
    elif choice == '4' and max_choice == 5:
        # Stage renames properly
        stage_renames(analyzer, files)
    elif choice == str(max_choice):
        return
    else:
        print("Invalid choice.")


def stage_renames(analyzer: ChangeAnalyzer, files: List[Dict]):
    """Stage renames properly using git rm + git add so git recognizes them."""
    print(f"\n{'=' * 80}")
    print("STAGE RENAMES")
    print("=" * 80)
    
    print(f"\nThis will tell git about {len(files)} rename(s):")
    for item in files:
        print(f"  {item['old']} → {item['new']}")
    
    print()
    try:
        confirm = input("Stage these renames? (y/n): ").strip().lower()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return
    
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        return
    
    print()
    for item in files:
        # Remove old file from git
        result = analyzer.run_git(["rm", "--cached", item['old']])
        if result.returncode == 0:
            print(f"  ✓ Removed: {item['old']}")
        else:
            print(f"  ✗ Failed to remove: {item['old']}")
        
        # Add new file to git
        result = analyzer.run_git(["add", item['new']])
        if result.returncode == 0:
            print(f"  ✓ Added: {item['new']}")
        else:
            print(f"  ✗ Failed to add: {item['new']}")
    
    print()
    print("✅ Renames staged! Git will now recognize these as renames.")
    print("   Run 'git status' to see the result.")
    print()
    input("Press Enter to continue...")


def show_shortstat(analyzer: ChangeAnalyzer, files: List[Dict]):
    """Show shortstat for files."""
    print(f"\n{'=' * 80}")
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    # Check if these are renames
    if files and 'old' in files[0]:
        # Calculate combined stats for all renames
        total_additions = 0
        total_deletions = 0
        files_changed = 0
        
        for item in files:
            try:
                # Get old content
                result_old = analyzer.run_git(["show", f"HEAD:{item['old']}"])
                old_lines = result_old.stdout.splitlines() if result_old.returncode == 0 else []
                
                # Get new content
                new_file = analyzer.repo_path / item['new']
                with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                    new_lines = f.read().splitlines()
                
                # Simple diff count
                import difflib
                diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                
                additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
                deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
                
                if additions > 0 or deletions > 0:
                    files_changed += 1
                    total_additions += additions
                    total_deletions += deletions
                    
            except Exception:
                pass
        
        print(f"\n{len(files)} file(s) renamed:")
        for item in files:
            print(f"  {item['old']} → {item['new']}")
        
        if files_changed > 0:
            print(f"\n{files_changed} file(s) changed, {total_additions} insertions(+), {total_deletions} deletions(-)")
        else:
            print("\n(all renames are identical - no content changes)")
        
        print()
        input("Press Enter to continue...")
        return
    
    # Get paths for git command
    paths = [f['path'] for f in files]
    
    # Show shortstat for modified files
    modified_paths = [f['path'] for f in files if f['status'] in ('M', 'MM')]
    if modified_paths:
        result = analyzer.run_git(["diff", "--shortstat", "HEAD", "--"] + modified_paths)
        if result.returncode == 0 and result.stdout:
            print("\nModified files:")
            print(result.stdout)
    
    # Count new/deleted files
    new_count = sum(1 for f in files if f['status'] in ('??', 'A'))
    deleted_count = sum(1 for f in files if f['status'] == 'D')
    
    if new_count > 0:
        print(f"\nNew files: {new_count}")
    if deleted_count > 0:
        print(f"Deleted files: {deleted_count}")
    
    print()
    input("Press Enter to continue...")


def show_stat(analyzer: ChangeAnalyzer, files: List[Dict]):
    """Show --stat for files."""
    print(f"\n{'=' * 80}")
    print("DETAILED FILE STATISTICS")
    print("=" * 80)
    
    # Check if these are renames
    if files and 'old' in files[0]:
        print(f"\n{len(files)} file(s) renamed:")
        for item in files:
            old_path = item['old']
            new_path = item['new']
            
            print(f"\n  📝 {old_path}")
            print(f"  →  {new_path}")
            
            # Calculate our own stats
            try:
                # Try to get old content from git (might be staged or from HEAD)
                result_old = analyzer.run_git(["show", f"HEAD:{old_path}"])
                if result_old.returncode != 0:
                    # Try staged version
                    result_old = analyzer.run_git(["show", f":{old_path}"])
                
                old_lines = result_old.stdout.splitlines() if result_old.returncode == 0 else []
                
                # Get new content from working directory or staged
                new_file = analyzer.repo_path / new_path
                if new_file.exists():
                    with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                        new_lines = f.read().splitlines()
                else:
                    # Try to get from index
                    result_new = analyzer.run_git(["show", f":{new_path}"])
                    new_lines = result_new.stdout.splitlines() if result_new.returncode == 0 else []
                
                # Simple diff count
                import difflib
                diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                
                additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
                deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
                
                if additions == 0 and deletions == 0:
                    print(f"     (identical - pure rename)")
                else:
                    print(f"     {additions} insertions(+), {deletions} deletions(-)")
                    
            except Exception as e:
                print(f"     (could not calculate stats: {e})")
        
        print()
        input("Press Enter to continue...")
        return
    
    # Regular files (not renames)
    for item in files:
        filepath = item['path']
        status = item['status']
        
        print(f"\n📄 {filepath}")
        
        # Check if this is actually a renamed file showing in code section
        if 'rename_from' in item:
            # This is a renamed file - show our own diff analysis
            old_path = item['rename_from']
            try:
                result_old = analyzer.run_git(["show", f"HEAD:{old_path}"])
                if result_old.returncode != 0:
                    result_old = analyzer.run_git(["show", f":{old_path}"])
                
                old_lines = result_old.stdout.splitlines() if result_old.returncode == 0 else []
                
                new_file = analyzer.repo_path / filepath
                if new_file.exists():
                    with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                        new_lines = f.read().splitlines()
                else:
                    result_new = analyzer.run_git(["show", f":{filepath}"])
                    new_lines = result_new.stdout.splitlines() if result_new.returncode == 0 else []
                
                import difflib
                diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                
                additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
                deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
                
                print(f"  (renamed from {old_path})")
                print(f"  {additions} insertions(+), {deletions} deletions(-)")
            except Exception as e:
                print(f"  (renamed from {old_path}, could not calculate stats: {e})")
        
        elif status == 'D':
            print("  [DELETED]")
        elif status in ('??', 'A'):
            try:
                with open(analyzer.repo_path / filepath, 'r') as f:
                    lines = len(f.readlines())
                print(f"  [NEW FILE - {lines} lines]")
            except:
                print("  [NEW FILE]")
        elif status in ('M', 'MM'):
            result = analyzer.run_git(["diff", "--stat", "HEAD", filepath])
            if result.returncode == 0:
                print(f"  {result.stdout.strip()}")
    
    print()
    input("Press Enter to continue...")


def show_full_diff(analyzer: ChangeAnalyzer, files: List[Dict]):
    """Show full diff for files."""
    print(f"\n{'=' * 80}")
    print("FULL DIFF")
    print("=" * 80)
    
    # Check if these are renames
    if files and 'old' in files[0]:
        for item in files:
            print(f"\n{'=' * 80}")
            print(f"RENAME: {item['old']} → {item['new']}")
            print("=" * 80)
            
            if not item.get('content_changed', False):
                print("\n✓ Files are identical - pure rename (no content changes)")
            else:
                print("\n⚠️  Content was modified during rename\n")
                # Show the actual diff
                try:
                    # Get old content
                    result_old = analyzer.run_git(["show", f"HEAD:{item['old']}"])
                    old_content = result_old.stdout if result_old.returncode == 0 else ""
                    
                    # Get new content
                    new_path = analyzer.repo_path / item['new']
                    with open(new_path, 'r', encoding='utf-8', errors='ignore') as f:
                        new_content = f.read()
                    
                    # Show unified diff
                    import difflib
                    diff = difflib.unified_diff(
                        old_content.splitlines(keepends=True),
                        new_content.splitlines(keepends=True),
                        fromfile=item['old'],
                        tofile=item['new'],
                        lineterm=''
                    )
                    print(''.join(diff))
                except Exception as e:
                    print(f"Could not generate diff: {e}")
        
        print()
        input("Press Enter to continue...")
        return
    
    # Regular files
    for item in files:
        filepath = item['path']
        status = item['status']
        
        print(f"\n📄 {filepath}")
        print("-" * 80)
        
        # Check if this is a renamed file in code section
        if 'rename_from' in item:
            old_path = item['rename_from']
            print(f"RENAMED from {old_path}\n")
            
            try:
                # Get old content
                result_old = analyzer.run_git(["show", f"HEAD:{old_path}"])
                if result_old.returncode != 0:
                    result_old = analyzer.run_git(["show", f":{old_path}"])
                old_content = result_old.stdout if result_old.returncode == 0 else ""
                
                # Get new content
                new_file = analyzer.repo_path / filepath
                if new_file.exists():
                    with open(new_file, 'r', encoding='utf-8', errors='ignore') as f:
                        new_content = f.read()
                else:
                    result_new = analyzer.run_git(["show", f":{filepath}"])
                    new_content = result_new.stdout if result_new.returncode == 0 else ""
                
                # Show unified diff
                import difflib
                diff = difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=old_path,
                    tofile=filepath,
                    lineterm=''
                )
                print(''.join(diff))
            except Exception as e:
                print(f"Could not generate diff: {e}")
        
        elif status == 'D':
            result = analyzer.run_git(["show", f"HEAD:{filepath}"])
            if result.returncode == 0:
                content = result.stdout
                lines = content.split('\n')
                preview = '\n'.join(lines[:30])
                print(f"DELETED FILE - Last content ({len(lines)} lines):\n{preview}")
                if len(lines) > 30:
                    print(f"\n... ({len(lines) - 30} more lines)")
        
        elif status in ('??', 'A'):
            try:
                with open(analyzer.repo_path / filepath, 'r') as f:
                    content = f.read()
                    lines = content.split('\n')
                    preview = '\n'.join(lines[:30])
                    print(f"NEW FILE ({len(lines)} lines):\n{preview}")
                    if len(lines) > 30:
                        print(f"\n... ({len(lines) - 30} more lines)")
            except Exception as e:
                print(f"  (Could not read: {e})")
        
        elif status in ('M', 'MM'):
            # Use git's colored diff output
            result = analyzer.run_git(["diff", "--color=always", "HEAD", filepath])
            if result.returncode == 0 and result.stdout:
                # Strip git metadata headers but keep colors
                lines = result.stdout.splitlines()
                cleaned_lines = []
                for line in lines:
                    plain = strip_ansi(line)
                    # Skip metadata
                    if plain.startswith(('diff --git', 'index ', '@@')):
                        continue
                    cleaned_lines.append(line)
                
                if cleaned_lines:
                    print('\n'.join(cleaned_lines))
            else:
                print(f"{Colors.DIM}(no changes){Colors.RESET}")
    
    print()
    input("Press Enter to continue...")


def show_combined_shortstat(analyzer: ChangeAnalyzer):
    """Show shortstat for ALL changes combined (staged + unstaged + untracked)."""
    # Staged changes
    result_staged = analyzer.run_git(["diff", "--shortstat", "--staged"])
    staged_text = result_staged.stdout.strip() if result_staged.returncode == 0 else ""
    
    # Unstaged changes
    result_unstaged = analyzer.run_git(["diff", "--shortstat"])
    unstaged_text = result_unstaged.stdout.strip() if result_unstaged.returncode == 0 else ""
    
    # Count untracked files
    result_status = analyzer.run_git(["status", "--porcelain"])
    untracked_count = 0
    untracked_lines = 0
    if result_status.returncode == 0:
        for line in result_status.stdout.strip().split('\n'):
            if line.startswith('??'):
                untracked_count += 1
                filepath = line[3:].strip()
                try:
                    file_path = analyzer.repo_path / filepath
                    if file_path.is_file():
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            untracked_lines += len(f.readlines())
                except:
                    pass
    
    print(f"{Colors.BOLD}Overall changes:{Colors.RESET}")
    if staged_text:
        print(f"  {Colors.CYAN}Staged:{Colors.RESET} {staged_text}")
    if unstaged_text:
        print(f"  {Colors.YELLOW}Unstaged:{Colors.RESET} {unstaged_text}")
    if untracked_count > 0:
        print(f"  {Colors.GREEN}Untracked:{Colors.RESET} {untracked_count} new files (~{untracked_lines} lines)")
    
    if not staged_text and not unstaged_text and untracked_count == 0:
        print(f"{Colors.DIM}(no changes){Colors.RESET}")


def show_combined_stat(analyzer: ChangeAnalyzer):
    """Show combined --stat for all changes (staged + unstaged + untracked)."""
    print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}COMBINED FILE STATISTICS{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}\n")
    
    # Staged
    result_staged = analyzer.run_git(["diff", "--stat", "--staged"])
    if result_staged.returncode == 0 and result_staged.stdout.strip():
        print(f"{Colors.CYAN}Staged changes:{Colors.RESET}")
        print(result_staged.stdout)
    
    # Unstaged
    result_unstaged = analyzer.run_git(["diff", "--stat"])
    if result_unstaged.returncode == 0 and result_unstaged.stdout.strip():
        print(f"{Colors.YELLOW}Unstaged changes:{Colors.RESET}")
        print(result_unstaged.stdout)
    
    # Untracked
    result_status = analyzer.run_git(["status", "--porcelain"])
    untracked = []
    if result_status.returncode == 0:
        for line in result_status.stdout.strip().split('\n'):
            if line.startswith('??'):
                untracked.append(line[3:].strip())
    
    if untracked:
        print(f"{Colors.GREEN}Untracked files ({len(untracked)} new):{Colors.RESET}")
        for filepath in untracked:
            try:
                file_path = analyzer.repo_path / filepath
                if file_path.is_file():
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = len(f.readlines())
                    print(f"  {filepath} | {lines} lines (new)")
            except:
                print(f"  {filepath} | (new)")
    
    if not result_staged.stdout.strip() and not result_unstaged.stdout.strip() and not untracked:
        print(f"{Colors.DIM}(no changes){Colors.RESET}")
    
    print()
    input("Press Enter to continue...")


def show_combined_diff(analyzer: ChangeAnalyzer):
    """Show combined full diff for all changes (staged + unstaged + preview of untracked)."""
    print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}COMBINED FULL DIFF{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}\n")
    
    # Staged
    result_staged = analyzer.run_git(["diff", "--color=always", "--staged"])
    if result_staged.returncode == 0 and result_staged.stdout.strip():
        print(f"{Colors.CYAN}{'=' * 80}{Colors.RESET}")
        print(f"{Colors.CYAN}STAGED CHANGES{Colors.RESET}")
        print(f"{Colors.CYAN}{'=' * 80}{Colors.RESET}\n")
        print(result_staged.stdout)
    
    # Unstaged
    result_unstaged = analyzer.run_git(["diff", "--color=always"])
    if result_unstaged.returncode == 0 and result_unstaged.stdout.strip():
        print(f"\n{Colors.YELLOW}{'=' * 80}{Colors.RESET}")
        print(f"{Colors.YELLOW}UNSTAGED CHANGES{Colors.RESET}")
        print(f"{Colors.YELLOW}{'=' * 80}{Colors.RESET}\n")
        print(result_unstaged.stdout)
    
    # Untracked (show preview)
    result_status = analyzer.run_git(["status", "--porcelain"])
    untracked = []
    if result_status.returncode == 0:
        for line in result_status.stdout.strip().split('\n'):
            if line.startswith('??'):
                untracked.append(line[3:].strip())
    
    if untracked:
        print(f"\n{Colors.GREEN}{'=' * 80}{Colors.RESET}")
        print(f"{Colors.GREEN}UNTRACKED FILES (preview of first 20 lines each){Colors.RESET}")
        print(f"{Colors.GREEN}{'=' * 80}{Colors.RESET}\n")
        for filepath in untracked:
            print(f"{Colors.GREEN}📄 {filepath} (NEW FILE){Colors.RESET}")
            print("-" * 80)
            try:
                file_path = analyzer.repo_path / filepath
                if file_path.is_file():
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        preview = ''.join(lines[:20])
                        print(preview)
                        if len(lines) > 20:
                            print(f"{Colors.DIM}... ({len(lines) - 20} more lines){Colors.RESET}")
            except Exception as e:
                print(f"{Colors.DIM}(could not read: {e}){Colors.RESET}")
            print()
    
    if not result_staged.stdout.strip() and not result_unstaged.stdout.strip() and not untracked:
        print(f"{Colors.DIM}(no changes){Colors.RESET}")
    
    input("\nPress Enter to continue...")


def interactive_commit(analyzer: ChangeAnalyzer):
    """Interactive commit workflow."""
    builder = CommitMessageBuilder(analyzer)
    
    print("\n" + "=" * 80)
    print("COMMIT PREPARATION")
    print("=" * 80)
    
    # Show comprehensive summary
    print("\n📊 CHANGES SUMMARY:")
    print("-" * 80)
    
    if analyzer.changes['renames']:
        print(f"\n{Colors.CYAN}🔄 Renames: {len(analyzer.changes['renames'])} files{Colors.RESET}")
        for item in analyzer.changes['renames']:
            status = f"{Colors.YELLOW} (with changes){Colors.RESET}" if item.get('content_changed') else f"{Colors.DIM} (identical){Colors.RESET}"
            print(f"  • {item['old']} → {item['new']}{status}")
    
    if analyzer.changes['code']:
        print(f"\n{Colors.GREEN}📝 Code: {len(analyzer.changes['code'])} files{Colors.RESET}")
        for item in analyzer.changes['code'][:5]:  # Show first 5
            if 'rename_from' in item:
                print(f"  • {item['path']} (renamed from {item['rename_from']})")
            else:
                # Status can be " M", "M ", "MM", etc - check if M is present
                status_name = "modified" if 'M' in item['status'] else "new"
                print(f"  • {item['path']} ({status_name})")
        if len(analyzer.changes['code']) > 5:
            print(f"  {Colors.DIM}... and {len(analyzer.changes['code']) - 5} more{Colors.RESET}")
    
    if analyzer.changes['translations']:
        lang_count = len(analyzer.changes['translations'])
        total_files = sum(len(files) for files in analyzer.changes['translations'].values())
        print(f"\n{Colors.MAGENTA}🌍 Translations: {total_files} files across {lang_count} languages{Colors.RESET}")
    
    if analyzer.changes['tests']:
        print(f"\n{Colors.YELLOW}🧪 Tests: {len(analyzer.changes['tests'])} files{Colors.RESET}")
    
    if analyzer.changes['docs']:
        print(f"\n{Colors.BLUE}📚 Docs: {len(analyzer.changes['docs'])} files{Colors.RESET}")
    
    if analyzer.changes['config']:
        print(f"\n{Colors.CYAN}⚙️  Config: {len(analyzer.changes['config'])} files{Colors.RESET}")
    
    if analyzer.changes['other']:
        print(f"\n{Colors.DIM}📦 Other: {len(analyzer.changes['other'])} files{Colors.RESET}")
    
    # Show shortstat for all changes
    print("\n" + "-" * 80)
    show_combined_shortstat(analyzer)
    
    print("\n" + "=" * 80)
    
    # Show suggested message
    suggested = builder.suggest_commit_message()
    print(f"\n{Colors.BOLD}Suggested commit message:{Colors.RESET}")
    print(f"  {suggested}")
    print()
    
    # Ask user preference
    print("Options:")
    print("  1. Use suggested message")
    print("  2. Write custom message")
    print("  3. View combined diff stats (--stat)")
    print("  4. View combined full diff (--patch)")
    print("  5. Cancel")
    print()
    
    try:
        choice = input("Choose option (1-5): ").strip()
    except KeyboardInterrupt:
        print("\n\nCommit cancelled.")
        return False
    
    if choice == '3':
        show_combined_stat(analyzer)
        print()
        return interactive_commit(analyzer)
    
    elif choice == '4':
        show_combined_diff(analyzer)
        print()
        return interactive_commit(analyzer)
    
    elif choice == '5':
        print("Commit cancelled.")
        return False
    
    elif choice == '2':
        print("\nEnter your commit message (press Ctrl+D when done):")
        message_lines = []
        try:
            while True:
                line = input()
                message_lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        
        message = '\n'.join(message_lines).strip()
        if not message:
            print("\nEmpty message. Commit cancelled.")
            return False
    
    else:  # Default to suggested
        message = suggested
    
    # Confirm
    print("\n" + "=" * 80)
    print("COMMIT CONFIRMATION")
    print("=" * 80)
    print(f"\nCommit message:\n{message}\n")
    
    # Count total files
    total_files = len(analyzer.changes['code']) + \
                  len(analyzer.changes['tests']) + \
                  len(analyzer.changes['docs']) + \
                  len(analyzer.changes['config']) + \
                  len(analyzer.changes['other']) + \
                  len(analyzer.changes['renames'])
    for lang_files in analyzer.changes['translations'].values():
        total_files += len(lang_files)
    
    print(f"Files to commit: {total_files}")
    print()
    
    try:
        confirm = input("Commit these changes? (y/n): ").strip().lower()
    except KeyboardInterrupt:
        print("\n\nCommit cancelled.")
        return False
    
    if confirm in ('y', 'yes'):
        # Add gitship marker to message
        marked_message = f"{message}\n\n[gitship-generated]"
        
        # Stage all changes
        result = analyzer.run_git(["add", "-A"])
        if result.returncode != 0:
            print(f"Error staging files: {result.stderr}")
            return False
        
        # Commit
        result = analyzer.run_git(["commit", "-m", marked_message])
        if result.returncode != 0:
            print(f"Error committing: {result.stderr}")
            return False
        
        print("\n✅ Changes committed successfully!")
        print(result.stdout)
        
        # ASK ABOUT PUSHING
        try:
            push = input("\nPush to remote? (y/n): ").strip().lower()
            if push in ('y', 'yes'):
                print(f"\n{Colors.CYAN}Pushing to remote...{Colors.RESET}")
                push_result = analyzer.run_git(["push"])
                if push_result.returncode == 0:
                    print(f"{Colors.GREEN}✓ Pushed to remote{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}⚠ Push failed: {push_result.stderr}{Colors.RESET}")
        except KeyboardInterrupt:
            print("\n\nPush skipped.")
        
        return True
    
    else:
        print("Commit cancelled.")
        return False


def clean_untracked_files(analyzer: ChangeAnalyzer):
    """Interactive cleanup of untracked files."""
    # Get all untracked files
    result = analyzer.run_git(["status", "--porcelain"])
    if result.returncode != 0:
        print("Error getting file status.")
        return
    
    untracked = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        if line.startswith('??'):
            filepath = line[3:].strip()
            untracked.append(filepath)
    
    if not untracked:
        print("\n✅ No untracked files to clean.")
        input("Press Enter to continue...")
        return
    
    print(f"\n{'=' * 80}")
    print("UNTRACKED FILES - Select files to delete")
    print("=" * 80)
    print(f"\nFound {len(untracked)} untracked files:")
    print()
    
    # Show files with numbers
    for i, filepath in enumerate(untracked, 1):
        path = Path(filepath)
        size = ""
        try:
            file_path = analyzer.repo_path / filepath
            if file_path.is_file():
                file_size = file_path.stat().st_size
                if file_size < 1024:
                    size = f" ({file_size}B)"
                elif file_size < 1024 * 1024:
                    size = f" ({file_size / 1024:.1f}KB)"
                else:
                    size = f" ({file_size / (1024 * 1024):.1f}MB)"
        except:
            pass
        
        print(f"  {i:2d}. {filepath}{size}")
    
    print()
    print("Options:")
    print("  - Enter file numbers to delete (e.g., '1 3 5' or '1-3')")
    print("  - Enter 'all' to delete all untracked files")
    print("  - Enter 'q' to cancel")
    print()
    
    try:
        selection = input("Select files to delete: ").strip()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return
    
    if selection.lower() == 'q':
        print("Cancelled.")
        return
    
    # Parse selection
    files_to_delete = []
    
    if selection.lower() == 'all':
        files_to_delete = untracked
    else:
        # Parse numbers and ranges
        for part in selection.split():
            if '-' in part:
                # Range like "1-3"
                try:
                    start, end = part.split('-')
                    start_idx = int(start) - 1
                    end_idx = int(end) - 1
                    if 0 <= start_idx < len(untracked) and 0 <= end_idx < len(untracked):
                        files_to_delete.extend(untracked[start_idx:end_idx + 1])
                except:
                    print(f"Invalid range: {part}")
            else:
                # Single number
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(untracked):
                        files_to_delete.append(untracked[idx])
                except:
                    print(f"Invalid number: {part}")
    
    if not files_to_delete:
        print("No files selected.")
        return
    
    # Confirm deletion
    print(f"\n{'=' * 80}")
    print("CONFIRM DELETION")
    print("=" * 80)
    print(f"\nFiles to delete ({len(files_to_delete)}):")
    for filepath in files_to_delete:
        print(f"  ❌ {filepath}")
    
    print()
    try:
        confirm = input("⚠️  Delete these files permanently? (yes/no): ").strip().lower()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return
    
    if confirm != 'yes':
        print("Deletion cancelled.")
        return
    
    # Delete files
    deleted_count = 0
    failed = []
    
    for filepath in files_to_delete:
        try:
            file_path = analyzer.repo_path / filepath
            if file_path.is_file():
                file_path.unlink()
                deleted_count += 1
                print(f"  ✓ Deleted: {filepath}")
            elif file_path.is_dir():
                import shutil
                shutil.rmtree(file_path)
                deleted_count += 1
                print(f"  ✓ Deleted: {filepath}")
        except Exception as e:
            failed.append((filepath, str(e)))
            print(f"  ✗ Failed: {filepath} - {e}")
    
    print()
    print(f"✅ Deleted {deleted_count} file(s)")
    
    if failed:
        print(f"❌ Failed to delete {len(failed)} file(s)")
    
    input("\nPress Enter to continue...")


def main_with_repo(repo_path: Path):
    """Main function for menu integration."""
    # Auto-scan dependencies before analyzing
    try:
        from gitship.deps import check_and_update_deps
        print(f"\n{Colors.DIM}Scanning dependencies...{Colors.RESET}")
        if check_and_update_deps(repo_path, silent=True):
            print(f"{Colors.GREEN}✓ Updated pyproject.toml with new dependencies{Colors.RESET}")
    except ImportError:
        pass

    analyzer = ChangeAnalyzer(repo_path)
    analyzer.analyze_changes()
    
    # Display summary
    analyzer.display_summary()
    
    # Main menu loop
    while True:
        print("\nWhat would you like to do?")
        print("  1. Review renames")
        print("  2. Review code changes")
        print("  3. Review translation changes")
        print("  4. Review test changes")
        print("  5. Review documentation changes")
        print("  6. Review config changes")
        print("  7. Clean untracked files (delete junk)")
        print("  8. Proceed to commit")
        print("  9. Exit")
        print()
        
        try:
            choice = input("Choose option (1-9): ").strip()
        except KeyboardInterrupt:
            print("\n\nCancelled.")
            sys.exit(0)
        
        if choice == '1' and analyzer.changes['renames']:
            show_diff_menu(analyzer, "Renamed Files", analyzer.changes['renames'])
        elif choice == '2' and analyzer.changes['code']:
            show_diff_menu(analyzer, "Code", analyzer.changes['code'])
        elif choice == '3' and analyzer.changes['translations']:
            # Flatten translations
            trans_files = []
            for lang_files in analyzer.changes['translations'].values():
                trans_files.extend(lang_files)
            show_diff_menu(analyzer, "Translations", trans_files)
        elif choice == '4' and analyzer.changes['tests']:
            show_diff_menu(analyzer, "Tests", analyzer.changes['tests'])
        elif choice == '5' and analyzer.changes['docs']:
            show_diff_menu(analyzer, "Documentation", analyzer.changes['docs'])
        elif choice == '6' and analyzer.changes['config']:
            show_diff_menu(analyzer, "Configuration", analyzer.changes['config'])
        elif choice == '7':
            clean_untracked_files(analyzer)
            # Re-analyze after cleaning
            analyzer.analyze_changes()
            analyzer.display_summary()
        elif choice == '8':
            interactive_commit(analyzer)
            break
        elif choice == '9':
            print("Exiting.")
            break
        else:
            if choice in ('1', '2', '3', '4', '5', '6', '7'):
                print("No changes in that category.")
            else:
                print("Invalid choice.")


def main():
    """Main entry point."""
    repo_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    
    # Check if it's a git repo
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_path,
        capture_output=True
    )
    
    if result.returncode != 0:
        print(f"Error: Not a git repository: {repo_path}")
        sys.exit(1)
    
    main_with_repo(repo_path)


if __name__ == "__main__":
    main()