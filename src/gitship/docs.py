#!/usr/bin/env python3
"""
docs - Documentation management for gitship.

Interactive README editor that lets you update sections individually.
"""
import shutil
import sys
import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple


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


def interactive_edit(repo_path: Path):
    """Interactive README editor."""
    readme_path = repo_path / "README.md"
    
    if not readme_path.exists():
        print("❌ README.md not found")
        print("\nCreate one first with: gitship docs --generate")
        return
    
    editor = ReadmeEditor(readme_path)
    
    while True:
        print("\n" + "=" * 60)
        print("README EDITOR")
        print("=" * 60)
        
        sections = editor.list_sections()
        print(f"\nCurrent sections ({len(sections)}):")
        for i, title in enumerate(sections, 1):
            section = editor.get_section(title)
            level = section['level']
            indent = "  " * (level - 1)
            print(f"  {i}. {indent}{title}")
        
        print("\nOptions:")
        print("  1. Edit section content")
        print("  2. Edit section title")
        print("  3. Edit title/tagline (top of file)")
        print("  4. Add new section")
        print("  5. Remove section")
        print("  6. Reorder sections")
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
            # Edit section content
            try:
                section_num = int(input("Section number to edit: ").strip())
                if 1 <= section_num <= len(sections):
                    title = sections[section_num - 1]
                    section = editor.get_section(title)
                    
                    print(f"\n{'=' * 60}")
                    print(f"Editing: {title}")
                    print("=" * 60)
                    print("\nCurrent content:")
                    print("-" * 60)
                    print(section['content'])
                    print("-" * 60)
                    
                    print("\nEnter new content (Ctrl+D to finish, Ctrl+C to cancel):")
                    lines = []
                    try:
                        while True:
                            line = input()
                            lines.append(line)
                    except EOFError:
                        new_content = '\n'.join(lines)
                        editor.update_section(title, new_content)
                        print("✅ Section updated")
                    except KeyboardInterrupt:
                        print("\n\nCancelled")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '2':
            # Edit section title
            try:
                section_num = int(input("Section number to edit title: ").strip())
                if 1 <= section_num <= len(sections):
                    old_title = sections[section_num - 1]
                    section = editor.get_section(old_title)
                    
                    print(f"\nCurrent title: {old_title}")
                    new_title = input("New title: ").strip()
                    
                    if new_title and new_title != old_title:
                        section['title'] = new_title
                        print("✅ Title updated")
                    else:
                        print("Cancelled")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '3':
            # Edit title/tagline
            print(f"\n{'=' * 60}")
            print("Edit Title/Tagline/Badges")
            print("=" * 60)
            print("\nCurrent:")
            print("-" * 60)
            print(editor.header)
            print("-" * 60)
            
            print("\n💡 Tip: Lines starting with # will be centered")
            print("💡 Badge lines should start with [![")
            print("\nEnter new header (Ctrl+D to finish, Ctrl+C to cancel):")
            lines = []
            try:
                while True:
                    line = input()
                    lines.append(line)
            except EOFError:
                # Process lines for centering - group consecutive badges/headers
                processed = []
                in_center_block = False
                center_lines = []
                
                for line in lines:
                    stripped = line.strip()
                    should_center = stripped.startswith('#') or stripped.startswith('[![')
                    
                    if should_center:
                        # Add to current center block
                        if not in_center_block:
                            in_center_block = True
                        center_lines.append(line)
                    else:
                        # End center block if needed
                        if in_center_block:
                            processed.append('<div align="center">')
                            processed.append('')
                            processed.extend(center_lines)
                            processed.append('')
                            processed.append('</div>')
                            center_lines = []
                            in_center_block = False
                        
                        # Add non-centered line
                        if line.strip():  # Skip empty lines
                            processed.append(line)
                
                # Handle any remaining center block
                if in_center_block and center_lines:
                    processed.append('<div align="center">')
                    processed.append('')
                    processed.extend(center_lines)
                    processed.append('')
                    processed.append('</div>')
                
                editor.header = '\n'.join(processed)
                print("✅ Title/tagline updated with centered formatting")
            except KeyboardInterrupt:
                print("\n\nCancelled")
        
        elif choice == '4':
            # Add section
            try:
                title = input("New section title: ").strip()
                if not title:
                    continue
                
                level = input("Header level (1-6, default 2): ").strip() or "2"
                level = int(level)
                if not 1 <= level <= 6:
                    level = 2
                
                print("Insert after which section? (leave blank for end)")
                for i, s in enumerate(sections, 1):
                    print(f"  {i}. {s}")
                
                after_input = input("After (number or blank): ").strip()
                after = None
                if after_input:
                    try:
                        after_num = int(after_input)
                        if 1 <= after_num <= len(sections):
                            after = sections[after_num - 1]
                    except ValueError:
                        pass
                
                print(f"\nEnter content for '{title}' (Ctrl+D to finish, Ctrl+C to cancel):")
                lines = []
                try:
                    while True:
                        line = input()
                        lines.append(line)
                except EOFError:
                    content = '\n'.join(lines)
                    editor.add_section(title, content, level, after)
                    print("✅ Section added")
                except KeyboardInterrupt:
                    print("\n\nCancelled")
            except Exception as e:
                print(f"Error: {e}")
        
        elif choice == '5':
            # Remove section
            try:
                section_num = int(input("Section number to remove: ").strip())
                if 1 <= section_num <= len(sections):
                    title = sections[section_num - 1]
                    confirm = input(f"Remove '{title}'? (y/n): ").strip().lower()
                    if confirm == 'y':
                        editor.remove_section(title)
                        print("✅ Section removed")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '6':
            # Reorder sections
            print("\nReorder section:")
            try:
                section_num = int(input("Section number to move: ").strip())
                if 1 <= section_num <= len(sections):
                    print("Move:")
                    print("  u - Up (swap with previous)")
                    print("  d - Down (swap with next)")
                    direction = input("Direction (u/d): ").strip().lower()
                    
                    if direction == 'u' and section_num > 1:
                        # Swap with previous
                        editor.sections[section_num-1], editor.sections[section_num-2] = \
                            editor.sections[section_num-2], editor.sections[section_num-1]
                        print("✅ Moved up")
                    elif direction == 'd' and section_num < len(sections):
                        # Swap with next
                        editor.sections[section_num-1], editor.sections[section_num] = \
                            editor.sections[section_num], editor.sections[section_num-1]
                        print("✅ Moved down")
                    else:
                        print("Can't move in that direction")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '7':
            # View section
            try:
                section_num = int(input("Section number to view: ").strip())
                if 1 <= section_num <= len(sections):
                    title = sections[section_num - 1]
                    section = editor.get_section(title)
                    
                    print(f"\n{'=' * 60}")
                    print(f"{section['level'] * '#'} {title}")
                    print("=" * 60)
                    print(section['content'])
                    print()
                    input("Press Enter to continue...")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '8':
            # Save and exit
            editor.save(backup=True)
            print("✅ Changes saved!")
            return
        
        elif choice == '4':
            # Add section
            try:
                title = input("New section title: ").strip()
                if not title:
                    continue
                
                level = input("Header level (1-6, default 2): ").strip() or "2"
                level = int(level)
                if not 1 <= level <= 6:
                    level = 2
                
                print("Insert after which section? (leave blank for end)")
                for i, s in enumerate(sections, 1):
                    print(f"  {i}. {s}")
                
                after_input = input("After (number or blank): ").strip()
                after = None
                if after_input:
                    try:
                        after_num = int(after_input)
                        if 1 <= after_num <= len(sections):
                            after = sections[after_num - 1]
                    except ValueError:
                        pass
                
                print(f"\nEnter content for '{title}' (Ctrl+D to finish, Ctrl+C to cancel):")
                lines = []
                try:
                    while True:
                        line = input()
                        lines.append(line)
                except EOFError:
                    content = '\n'.join(lines)
                    editor.add_section(title, content, level, after)
                    print("✅ Section added")
                except KeyboardInterrupt:
                    print("\n\nCancelled")
            except Exception as e:
                print(f"Error: {e}")
        
        elif choice == '4':
            # Remove section
            try:
                section_num = int(input("Section number to remove: ").strip())
                if 1 <= section_num <= len(sections):
                    title = sections[section_num - 1]
                    confirm = input(f"Remove '{title}'? (y/n): ").strip().lower()
                    if confirm == 'y':
                        editor.remove_section(title)
                        print("✅ Section removed")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '5':
            # Reorder sections
            print("\nReordering not yet implemented")
            print("Tip: Remove and re-add sections to change order")
        
        elif choice == '6':
            # View section
            try:
                section_num = int(input("Section number to view: ").strip())
                if 1 <= section_num <= len(sections):
                    title = sections[section_num - 1]
                    section = editor.get_section(title)
                    
                    print(f"\n{'=' * 60}")
                    print(f"{section['level'] * '#'} {title}")
                    print("=" * 60)
                    print(section['content'])
                    print()
                    input("Press Enter to continue...")
                else:
                    print("Invalid section number")
            except ValueError:
                print("Invalid input")
        
        elif choice == '7':
            # Save and exit
            editor.save(backup=True)
            print("✅ Changes saved!")
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


def main_with_args(repo_path: Path, source: Optional[str] = None, generate: bool = False, edit: bool = False):
    """Entry point for docs command."""
    
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
    
    # No arguments - show usage
    print("README Management:")
    print("  gitship docs --edit       Interactive section editor")
    print("  gitship docs --generate   Create template README")
    print("  gitship docs --source <file>   Replace from file")