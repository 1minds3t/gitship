#!/usr/bin/env python3
"""
docs - Documentation management for gitship.

Interactive README editor that lets you update sections individually.
Also integrates with docbuilder.py for full MkDocs site documentation management.
"""
import shutil
import sys
import re
import subprocess
import importlib
from pathlib import Path
from typing import Optional, List, Dict, Tuple


# ─── Dependency helpers ────────────────────────────────────────────────────────

DOCBUILDER_DEPS = {
    "ruamel.yaml": "ruamel.yaml",   # import name → pip package name
}

def _check_deps(deps: dict) -> list:
    """Return list of (import_name, pip_name) for any missing packages."""
    missing = []
    for import_name, pip_name in deps.items():
        try:
            importlib.import_module(import_name.replace("-", "_").replace(".", "_").split(".")[0]
                                    if import_name != "ruamel.yaml" else "ruamel")
        except ImportError:
            missing.append((import_name, pip_name))
    return missing


def _offer_install(missing: list) -> bool:
    """
    Offer to pip install missing packages.
    Returns True if all deps are now satisfied (installed or already present).
    """
    if not missing:
        return True

    print("\n⚠️  docbuilder requires the following package(s) not currently installed:")
    for import_name, pip_name in missing:
        print(f"   • {pip_name}")

    try:
        answer = input("\nInstall now? [Y/n]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False

    if answer in ("", "y", "yes"):
        pip_packages = [pip_name for _, pip_name in missing]
        cmd = [sys.executable, "-m", "pip", "install"] + pip_packages
        print(f"\n▶  Running: {' '.join(cmd)}\n")
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("\n✅ Dependencies installed successfully!")
            return True
        else:
            print("\n❌ Installation failed. You can install manually:")
            print(f"   pip install {' '.join(pip_packages)}")
            return False
    else:
        print("\nSkipping install. docbuilder will not be available this session.")
        return False


def launch_docbuilder(repo_path: Path, dry_run: bool = False):
    """Check deps, then launch DocBuilder interactive menu."""
    missing = _check_deps(DOCBUILDER_DEPS)
    if missing:
        ok = _offer_install(missing)
        if not ok:
            return

    # Now safe to import
    try:
        # Look for docbuilder.py next to this file
        docbuilder_path = Path(__file__).parent / "docbuilder.py"
        if not docbuilder_path.exists():
            print(f"❌ docbuilder.py not found at {docbuilder_path}")
            print("   Make sure docbuilder.py is in the same directory as docs.py")
            return

        import importlib.util
        spec = importlib.util.spec_from_file_location("docbuilder", docbuilder_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        builder = mod.DocBuilder(dry_run=dry_run, root=repo_path)
        builder.main_menu()

    except KeyboardInterrupt:
        print("\n\n👋 Returned to docs menu.")
    except Exception as e:
        print(f"\n❌ docbuilder error: {e}")


# ─── End dependency helpers ────────────────────────────────────────────────────


class ReadmeEditor:
    """Parse and edit README.md files section by section."""
    
    def __init__(self, readme_path: Path):
        self.path = readme_path
        self.sections = []
        self.header = ""  # Content before first section
        
        if readme_path.exists():
            self._parse()
    
    def _parse(self):
        """Parse README into sections based on headers."""
        content = self.path.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        current_section = None
        current_content = []
        header_lines = []
        in_html_block = False
        
        for line in lines:
            # Track if we're inside HTML tags (like <div align="center">)
            if '<div' in line.lower():
                in_html_block = True
            if '</div>' in line.lower():
                in_html_block = False
                # Don't check this line for headers either
                if current_section:
                    current_content.append(line)
                else:
                    header_lines.append(line)
                continue
            
            # Only check for headers if NOT inside HTML block
            if not in_html_block:
                # Check if this is a markdown header (## or more, not single #)
                header_match = re.match(r'^(#{2,6})\s+(.+)$', line)
                
                if header_match:
                    # Save previous section if exists
                    if current_section:
                        self.sections.append({
                            'level': current_section['level'],
                            'title': current_section['title'],
                            'content': '\n'.join(current_content).strip()
                        })
                        current_content = []
                    else:
                        # This is before the first section - save as header
                        self.header = '\n'.join(header_lines).strip()
                        header_lines = []
                    
                    # Start new section
                    level = len(header_match.group(1))
                    title = header_match.group(2)
                    current_section = {'level': level, 'title': title}
                    continue
            
            # Content line
            if current_section:
                current_content.append(line)
            else:
                header_lines.append(line)
        
        # Save last section
        if current_section:
            self.sections.append({
                'level': current_section['level'],
                'title': current_section['title'],
                'content': '\n'.join(current_content).strip()
            })
        elif header_lines:
            self.header = '\n'.join(header_lines).strip()
    
    def get_section(self, title: str) -> Optional[Dict]:
        """Get a section by title (case-insensitive)."""
        title_lower = title.lower()
        for section in self.sections:
            if section['title'].lower() == title_lower:
                return section
        return None
    
    def update_section(self, title: str, new_content: str):
        """Update a section's content."""
        for section in self.sections:
            if section['title'].lower() == title.lower():
                section['content'] = new_content.strip()
                return True
        return False
    
    def add_section(self, title: str, content: str, level: int = 2, after: Optional[str] = None):
        """Add a new section."""
        new_section = {
            'level': level,
            'title': title,
            'content': content.strip()
        }
        
        if after:
            # Insert after specific section
            for i, section in enumerate(self.sections):
                if section['title'].lower() == after.lower():
                    self.sections.insert(i + 1, new_section)
                    return True
            # If not found, append
            self.sections.append(new_section)
        else:
            # Append at end
            self.sections.append(new_section)
        
        return True
    
    def remove_section(self, title: str) -> bool:
        """Remove a section by title."""
        for i, section in enumerate(self.sections):
            if section['title'].lower() == title.lower():
                self.sections.pop(i)
                return True
        return False
    
    def list_sections(self) -> List[str]:
        """Get list of all section titles."""
        return [s['title'] for s in self.sections]
    
    def to_markdown(self) -> str:
        """Convert back to markdown format."""
        lines = []
        
        # Add header (content before first section)
        if self.header:
            lines.append(self.header)
            lines.append('')
        
        # Add sections
        for section in self.sections:
            # Add section header
            header_prefix = '#' * section['level']
            lines.append(f"{header_prefix} {section['title']}")
            
            # Add section content
            if section['content']:
                lines.append(section['content'])
            
            lines.append('')  # Blank line after section
        
        return '\n'.join(lines).rstrip() + '\n'
    
    def save(self, backup: bool = True):
        """Save changes to README."""
        if backup and self.path.exists():
            backup_path = self.path.with_suffix('.md.bak')
            shutil.copy(self.path, backup_path)
            print(f"📦 Backed up to {backup_path.name}")
            
            # Add .bak to gitignore if not already there
            gitignore_path = self.path.parent / '.gitignore'
            try:
                if gitignore_path.exists():
                    gitignore_content = gitignore_path.read_text(encoding='utf-8')
                    if '*.bak' not in gitignore_content and '.bak' not in gitignore_content:
                        with open(gitignore_path, 'a', encoding='utf-8') as f:
                            f.write('\n# Backup files\n*.bak\n')
                        print("📝 Added *.bak to .gitignore")
                else:
                    # Create .gitignore with .bak entry
                    gitignore_path.write_text('# Backup files\n*.bak\n', encoding='utf-8')
                    print("📝 Created .gitignore with *.bak")
            except Exception as e:
                # Don't fail if gitignore update fails
                pass
        
        self.path.write_text(self.to_markdown(), encoding='utf-8')
        print(f"✅ Saved {self.path.name}")



def _clean_title(raw_title: str, max_len: int = 60) -> str:
    """
    Strip markdown badges/links from a section title for display purposes.
    The raw title is NEVER modified — this is display-only.
    """
    t = re.sub(r'\[!\[.*?\]\(.*?\)\]\(.*?\)', '', raw_title)   # badge links
    t = re.sub(r'!\[.*?\]\(.*?\)', '', t)                       # plain images
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)              # plain links → text
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > max_len:
        t = t[:max_len - 1] + '\u2026'
    return t or raw_title[:max_len]


def _has_badges(raw_title: str) -> bool:
    return bool(re.search(r'!\[', raw_title))


def _build_flat_index(sections: List[Dict]) -> List[Dict]:
    """Build display list: clean titles, indented by heading level, badge hint."""
    result = []
    for i, sec in enumerate(sections):
        indent = '  ' * max(0, sec['level'] - 2)
        clean = _clean_title(sec['title'])
        badge_hint = ' \U0001f3f7' if _has_badges(sec['title']) else ''
        result.append({
            'num': i + 1,
            'section': sec,
            'display_label': f"{indent}{clean}{badge_hint}",
            'index': i,
        })
    return result


def _pick_section(flat: List[Dict], prompt: str) -> 'Optional[Dict]':
    """Prompt for a section number; return the flat entry or None."""
    try:
        raw = input(prompt).strip()
        if not raw:
            return None
        num = int(raw)
        matches = [e for e in flat if e['num'] == num]
        if not matches:
            print(f"\u274c No section #{num}")
            return None
        entry = matches[0]
        if _has_badges(entry['section']['title']):
            preview = entry['section']['title']
            if len(preview) > 80:
                preview = preview[:79] + '\u2026'
            print(f"  \u2192 Full title: {preview}")
        return entry
    except ValueError:
        print("\u274c Please enter a number")
        return None


def interactive_edit(repo_path: Path):
    """Interactive README editor with clean badge-safe display."""
    readme_path = repo_path / "README.md"

    if not readme_path.exists():
        print("\u274c README.md not found")
        print("\nCreate one first with: gitship docs --generate")
        return

    editor = ReadmeEditor(readme_path)

    while True:
        print("\n" + "=" * 60)
        print("README EDITOR")
        print("=" * 60)

        flat = _build_flat_index(editor.sections)
        print(f"\nSections ({len(flat)})   \U0001f3f7 = title contains badges")
        for entry in flat:
            print(f"  {entry['num']:>3}. {entry['display_label']}")

        print("\nOptions:")
        print("  1. Edit section content")
        print("  2. Edit section title")
        print("  3. Edit title/tagline (top of file)")
        print("  4. Add new section")
        print("  5. Remove section")
        print("  6. Reorder section (move up/down)")
        print("  7. View section")
        print("  8. Save and exit")
        print("  0. Cancel (don't save)")

        try:
            choice = input("\nChoice (0-8): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nCancelled.")
            return

        if choice == '0':
            print("Cancelled - no changes saved")
            return

        elif choice == '1':
            entry = _pick_section(flat, "Section number to edit: ")
            if not entry:
                continue
            sec = entry['section']
            print(f"\n{'=' * 60}")
            print(f"Editing: {_clean_title(sec['title'])}")
            print("=" * 60)
            print("\nCurrent content:")
            print("-" * 60)
            print(sec['content'])
            print("-" * 60)
            print("\nEnter new content (Ctrl+D to finish, Ctrl+C to cancel):")
            lines = []
            try:
                while True:
                    lines.append(input())
            except EOFError:
                sec['content'] = '\n'.join(lines).strip()
                print("\u2705 Section updated")
            except KeyboardInterrupt:
                print("\n\nCancelled")

        elif choice == '2':
            entry = _pick_section(flat, "Section number to rename: ")
            if not entry:
                continue
            sec = entry['section']
            print(f"\nCurrent title (raw): {sec['title']}")
            print(f"Display title:       {_clean_title(sec['title'])}")
            print("\U0001f4a1 To keep badges, paste the full title back with your edits.")
            new_title = input("New title (blank to cancel): ").strip()
            if new_title:
                sec['title'] = new_title
                print("\u2705 Title updated")
            else:
                print("Cancelled")

        elif choice == '3':
            print(f"\n{'=' * 60}")
            print("Edit Title / Tagline / Badges")
            print("=" * 60)
            print("\nCurrent:")
            print("-" * 60)
            print(editor.header)
            print("-" * 60)
            print("\n\U0001f4a1 Lines starting with # or [! will be auto-centered")
            print("Enter new header (Ctrl+D to finish, Ctrl+C to cancel):")
            lines = []
            try:
                while True:
                    lines.append(input())
            except EOFError:
                processed = []
                in_center = False
                center_buf = []
                for line in lines:
                    s = line.strip()
                    if s.startswith('#') or s.startswith('[!['):
                        in_center = True
                        center_buf.append(line)
                    else:
                        if in_center:
                            processed += ['<div align="center">', ''] + center_buf + ['', '</div>']
                            center_buf = []
                            in_center = False
                        if s:
                            processed.append(line)
                if in_center and center_buf:
                    processed += ['<div align="center">', ''] + center_buf + ['', '</div>']
                editor.header = '\n'.join(processed)
                print("\u2705 Header updated")
            except KeyboardInterrupt:
                print("\n\nCancelled")

        elif choice == '4':
            try:
                title = input("New section title: ").strip()
                if not title:
                    continue
                try:
                    level = int(input("Header level (2-6, default 2): ").strip() or "2")
                    if not 2 <= level <= 6:
                        level = 2
                except ValueError:
                    level = 2
                print("Insert after which section? (blank = end)")
                for entry in flat:
                    print(f"  {entry['num']:>3}. {entry['display_label']}")
                after_raw = input("After # (blank = end): ").strip()
                after = None
                if after_raw:
                    try:
                        after_num = int(after_raw)
                        matches = [e for e in flat if e['num'] == after_num]
                        if matches:
                            after = matches[0]['section']['title']
                    except ValueError:
                        pass
                print(f"\nEnter content for '{title}' (Ctrl+D to finish, Ctrl+C to cancel):")
                lines = []
                try:
                    while True:
                        lines.append(input())
                except EOFError:
                    editor.add_section(title, '\n'.join(lines), level, after)
                    print("\u2705 Section added")
                except KeyboardInterrupt:
                    print("\n\nCancelled")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == '5':
            entry = _pick_section(flat, "Section number to remove: ")
            if not entry:
                continue
            sec = entry['section']
            confirm = input(f"Remove '{_clean_title(sec['title'])}'? (y/n): ").strip().lower()
            if confirm == 'y':
                editor.sections.remove(sec)
                print("\u2705 Section removed")
            else:
                print("Cancelled")

        elif choice == '6':
            entry = _pick_section(flat, "Section number to move: ")
            if not entry:
                continue
            idx = entry['index']
            n = len(editor.sections)
            print("  u - Move up   d - Move down")
            direction = input("Direction (u/d): ").strip().lower()
            if direction == 'u' and idx > 0:
                editor.sections[idx], editor.sections[idx - 1] = \
                    editor.sections[idx - 1], editor.sections[idx]
                print("\u2705 Moved up")
            elif direction == 'd' and idx < n - 1:
                editor.sections[idx], editor.sections[idx + 1] = \
                    editor.sections[idx + 1], editor.sections[idx]
                print("\u2705 Moved down")
            else:
                print("Can't move in that direction")

        elif choice == '7':
            entry = _pick_section(flat, "Section number to view: ")
            if not entry:
                continue
            sec = entry['section']
            print(f"\n{'=' * 60}")
            print(f"{'#' * sec['level']} {sec['title']}")
            print("=" * 60)
            print(sec['content'])
            print()
            input("Press Enter to continue...")

        elif choice == '8':
            editor.save(backup=True)
            return



def generate_default_readme(repo_path: Path) -> str:
    """Generate a modern README template."""
    project_name = repo_path.name
    
    return f"""# {project_name} 🚀

**One-line description of your project**

Brief overview of what this project does and why it exists.

## ✨ Features

- Feature 1
- Feature 2
- Feature 3

## 🚀 Installation

```bash
pip install {project_name}
```

## 📖 Usage

```python
import {project_name}

# Example usage
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

MIT License
"""


def main_with_args(repo_path: Path, source: Optional[str] = None, generate: bool = False,
                   edit: bool = False, mkdocs: bool = False, dry_run: bool = False,
                   deploy: bool = False):
    """Entry point for docs command."""

    if deploy:
        mod = _load_deploy_module(repo_path)
        if mod:
            try:
                mod.main_menu(repo_path)
            except KeyboardInterrupt:
                print("\n👋 Returned.")
        return

    if edit:
        interactive_edit(repo_path)
        return

    if generate:
        readme_path = repo_path / "README.md"

        if readme_path.exists():
            backup = readme_path.with_suffix(".md.bak")
            shutil.copy(readme_path, backup)
            print(f"📦 Backed up existing README to {backup.name}")

        content = generate_default_readme(repo_path)
        readme_path.write_text(content, encoding='utf-8')
        print(f"✅ Generated new README.md")
        return

    if source:
        # Legacy: replace entire file from source
        readme_path = repo_path / "README.md"
        source_path = Path(source)

        if not source_path.exists():
            print(f"❌ Source file not found: {source_path}")
            return

        if readme_path.exists():
            backup = readme_path.with_suffix(".md.bak")
            shutil.copy(readme_path, backup)
            print(f"📦 Backed up existing README to {backup.name}")

        shutil.copy(source_path, readme_path)
        print(f"✅ Updated README.md from {source_path.name}")
        return

    if mkdocs:
        launch_docbuilder(repo_path, dry_run=dry_run)
        return

    # No arguments - show unified top-level menu
    _docs_top_menu(repo_path, dry_run=dry_run)


def _load_deploy_module(repo_path: Path):
    """Dynamically load mkdocs_deploy.py from alongside docs.py."""
    import importlib.util
    deploy_path = Path(__file__).parent / "mkdocs_deploy.py"
    if not deploy_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("mkdocs_deploy", deploy_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"\n⚠️  Could not load mkdocs_deploy.py: {e}")
        return None


def _docs_top_menu(repo_path: Path, dry_run: bool = False):
    """Top-level docs menu."""
    docbuilder_path = Path(__file__).parent / "docbuilder.py"
    deploy_path     = Path(__file__).parent / "mkdocs_deploy.py"
    has_docbuilder  = docbuilder_path.exists()
    has_deploy      = deploy_path.exists()

    while True:
        print("\n" + "=" * 60)
        print("📚 DOCS - Documentation Manager")
        print("=" * 60)
        print("  1. README editor       — edit/generate README.md sections")
        if has_docbuilder:
            print("  2. MkDocs site builder — create & manage docs/ site pages")
        else:
            print("  2. MkDocs site builder — ⚠️  docbuilder.py not found")
        if has_deploy:
            print("  3. Deploy docs         — local server / GitHub Pages / systemd")
        else:
            print("  3. Deploy docs         — ⚠️  mkdocs_deploy.py not found")
        print("  0. Back")
        print()
        print("  Or use flags directly:")
        print("    gitship docs --edit             README section editor")
        print("    gitship docs --generate         Create template README")
        print("    gitship docs --source <f>       Replace README from file")
        if has_docbuilder:
            print("    gitship docs --mkdocs           MkDocs site builder")
            print("    gitship docs --mkdocs --dry-run Preview only")
        if has_deploy:
            print("    gitship docs --deploy           Deployment menu")

        try:
            choice = input("\nChoice (0-3): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "0":
            return
        elif choice == "1":
            interactive_edit(repo_path)
        elif choice == "2":
            if has_docbuilder:
                launch_docbuilder(repo_path, dry_run=dry_run)
            else:
                print(f"\n❌ docbuilder.py not found — place it alongside docs.py")
        elif choice == "3":
            if has_deploy:
                mod = _load_deploy_module(repo_path)
                if mod:
                    try:
                        mod.main_menu(repo_path)
                    except KeyboardInterrupt:
                        print("\n👋 Returned to docs menu.")
            else:
                print(f"\n❌ mkdocs_deploy.py not found — place it alongside docs.py")
        else:
            print("Invalid choice")