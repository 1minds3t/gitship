#!/usr/bin/env python3
"""
gitignore - Manage .gitignore entries from CLI
"""
from pathlib import Path
from typing import List, Optional


def read_gitignore(repo_path: Path) -> List[str]:
    """Read current .gitignore entries."""
    gitignore_path = repo_path / '.gitignore'
    
    if not gitignore_path.exists():
        return []
    
    try:
        content = gitignore_path.read_text(encoding='utf-8')
        # Return non-empty, non-comment lines
        return [line.strip() for line in content.split('\n') 
                if line.strip() and not line.strip().startswith('#')]
    except Exception as e:
        print(f"Error reading .gitignore: {e}")
        return []


def add_to_gitignore(repo_path: Path, pattern: str, comment: Optional[str] = None):
    """Add a pattern to .gitignore."""
    gitignore_path = repo_path / '.gitignore'
    
    # Check if pattern already exists
    existing = read_gitignore(repo_path)
    if pattern in existing:
        print(f"✓ '{pattern}' already in .gitignore")
        return
    
    try:
        # Append to existing or create new
        mode = 'a' if gitignore_path.exists() else 'w'
        with open(gitignore_path, mode, encoding='utf-8') as f:
            # Add newline if file exists and doesn't end with one
            if mode == 'a':
                content = gitignore_path.read_text(encoding='utf-8')
                if content and not content.endswith('\n'):
                    f.write('\n')
            
            # Add comment if provided
            if comment:
                f.write(f'\n# {comment}\n')
            
            # Add pattern
            f.write(f'{pattern}\n')
        
        print(f"✅ Added '{pattern}' to .gitignore")
        
    except Exception as e:
        print(f"❌ Error writing to .gitignore: {e}")


def remove_from_gitignore(repo_path: Path, pattern: str):
    """Remove a pattern from .gitignore."""
    gitignore_path = repo_path / '.gitignore'
    
    if not gitignore_path.exists():
        print("❌ .gitignore not found")
        return
    
    try:
        content = gitignore_path.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        # Remove the pattern line
        new_lines = []
        removed = False
        for line in lines:
            if line.strip() == pattern:
                removed = True
                continue
            new_lines.append(line)
        
        if removed:
            gitignore_path.write_text('\n'.join(new_lines), encoding='utf-8')
            print(f"✅ Removed '{pattern}' from .gitignore")
        else:
            print(f"⚠️  '{pattern}' not found in .gitignore")
            
    except Exception as e:
        print(f"❌ Error updating .gitignore: {e}")


def list_gitignore(repo_path: Path):
    """Display current .gitignore entries."""
    gitignore_path = repo_path / '.gitignore'
    
    if not gitignore_path.exists():
        print("❌ .gitignore not found")
        print("💡 Create one with: gitship ignore --add <pattern>")
        return
    
    try:
        content = gitignore_path.read_text(encoding='utf-8')
        
        if not content.strip():
            print("📝 .gitignore is empty")
            return
        
        print("\n📝 .gitignore entries:")
        print("=" * 60)
        
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            
            if stripped.startswith('#'):
                # Comment line
                print(f"\033[90m{line}\033[0m")  # Gray
            else:
                # Pattern line
                print(f"  {line}")
        
        print()
        
    except Exception as e:
        print(f"❌ Error reading .gitignore: {e}")


def add_common_patterns(repo_path: Path, language: str = 'python'):
    """Add common ignore patterns for a language."""
    patterns = {
        'python': [
            ('__pycache__/', 'Python cache'),
            ('*.py[cod]', 'Python compiled'),
            ('*$py.class', 'Python compiled'),
            ('*.so', 'C extensions'),
            ('.Python', 'Python'),
            ('build/', 'Build artifacts'),
            ('develop-eggs/', 'Build artifacts'),
            ('dist/', 'Distribution'),
            ('downloads/', 'Build artifacts'),
            ('eggs/', 'Build artifacts'),
            ('.eggs/', 'Build artifacts'),
            ('lib/', 'Build artifacts'),
            ('lib64/', 'Build artifacts'),
            ('parts/', 'Build artifacts'),
            ('sdist/', 'Build artifacts'),
            ('var/', 'Build artifacts'),
            ('wheels/', 'Build artifacts'),
            ('*.egg-info/', 'Python package'),
            ('.installed.cfg', 'Build artifacts'),
            ('*.egg', 'Python package'),
            ('.pytest_cache/', 'Testing'),
            ('.coverage', 'Testing'),
            ('htmlcov/', 'Testing'),
            ('.env', 'Environment'),
            ('.venv', 'Virtual environment'),
            ('env/', 'Virtual environment'),
            ('venv/', 'Virtual environment'),
            ('*.bak', 'Backup files'),
        ],
        'node': [
            ('node_modules/', 'Dependencies'),
            ('npm-debug.log*', 'Logs'),
            ('yarn-debug.log*', 'Logs'),
            ('yarn-error.log*', 'Logs'),
            ('.npm', 'npm'),
            ('.env', 'Environment'),
            ('dist/', 'Build'),
            ('build/', 'Build'),
        ]
    }
    
    if language not in patterns:
        print(f"❌ Unknown language: {language}")
        print(f"Available: {', '.join(patterns.keys())}")
        return
    
    print(f"\n📝 Adding common {language} patterns to .gitignore...")
    
    for pattern, comment in patterns[language]:
        existing = read_gitignore(repo_path)
        if pattern not in existing:
            add_to_gitignore(repo_path, pattern, comment)


def ensure_self_ignored(repo_path: Path):
    """Silently ensure gitship's own directories are in .gitignore.

    Called on every CLI startup. No output if already ignored.
    """
    patterns_needed = [
        ('.gitship/',        'gitship internal state (auto-added by gitship)'),
        ('gitship_exports/', 'gitship diff exports (auto-added by gitship)'),
    ]
    gitignore_path = repo_path / '.gitignore'
    existing = read_gitignore(repo_path)

    added_any = False
    for pattern, comment in patterns_needed:
        if pattern not in existing:
            try:
                mode = 'a' if gitignore_path.exists() else 'w'
                with open(gitignore_path, mode, encoding='utf-8') as f:
                    if mode == 'a':
                        content = gitignore_path.read_text(encoding='utf-8')
                        if content and not content.endswith('\n'):
                            f.write('\n')
                    f.write(f'\n# {comment}\n')
                    f.write(f'{pattern}\n')
                added_any = True
            except Exception:
                pass  # Never crash startup over a gitignore write

    if added_any:
        # Auto-stage .gitignore so the addition is visible in the next commit
        try:
            import subprocess
            subprocess.run(
                ['git', 'add', '.gitignore'],
                cwd=repo_path, capture_output=True, check=False
            )
        except Exception:
            pass


def main_with_args(repo_path: Path, add: Optional[str] = None, remove: Optional[str] = None, 
                   list_entries: bool = False, common: Optional[str] = None):
    """Entry point for gitignore command."""
    
    if add:
        # Add pattern
        comment = input("Optional comment (or press Enter): ").strip()
        add_to_gitignore(repo_path, add, comment if comment else None)
    
    elif remove:
        # Remove pattern
        remove_from_gitignore(repo_path, remove)
    
    elif common:
        # Add common patterns
        add_common_patterns(repo_path, common)
    
    elif list_entries:
        # List current entries
        list_gitignore(repo_path)
    
    else:
        # Interactive menu
        interactive_gitignore(repo_path)


def interactive_gitignore(repo_path: Path):
    """Interactive gitignore management."""
    while True:
        print("\n" + "=" * 60)
        print("GITIGNORE MANAGER")
        print("=" * 60)
        
        print("\nOptions:")
        print("  1. View .gitignore")
        print("  2. Add pattern")
        print("  3. Remove pattern")
        print("  4. Add common patterns (Python/Node)")
        print("  0. Exit")
        
        try:
            choice = input("\nChoice (0-4): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return
        
        if choice == '0':
            return
        
        elif choice == '1':
            list_gitignore(repo_path)
            input("\nPress Enter to continue...")
        
        elif choice == '2':
            pattern = input("Pattern to add (e.g., *.log, __pycache__/): ").strip()
            if pattern:
                comment = input("Optional comment: ").strip()
                add_to_gitignore(repo_path, pattern, comment if comment else None)
        
        elif choice == '3':
            list_gitignore(repo_path)
            pattern = input("\nPattern to remove: ").strip()
            if pattern:
                remove_from_gitignore(repo_path, pattern)
        
        elif choice == '4':
            print("\nLanguage:")
            print("  1. Python")
            print("  2. Node.js")
            lang_choice = input("Choice (1-2): ").strip()
            
            if lang_choice == '1':
                add_common_patterns(repo_path, 'python')
            elif lang_choice == '2':
                add_common_patterns(repo_path, 'node')