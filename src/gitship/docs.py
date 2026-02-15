#!/usr/bin/env python3
"""
docs - Documentation management for gitship.
"""
import shutil
import sys
from pathlib import Path
from typing import Optional

def update_readme(source_path: Path, repo_path: Path = None) -> bool:
    """
    Update README.md from a source file.
    Creates a backup of the existing README.
    """
    if repo_path is None:
        repo_path = Path.cwd()
    
    dest = repo_path / "README.md"
    
    if not source_path.exists():
        print(f"Error: Source file not found: {source_path}")
        return False
        
    if dest.exists():
        backup = dest.with_suffix(".md.bak")
        try:
            shutil.copy(dest, backup)
            print(f"📦 Backed up existing README to {backup.name}")
        except Exception as e:
            print(f"Warning: Could not create backup: {e}")
            
    try:
        shutil.copy(source_path, dest)
        print(f"✅ Updated README.md from {source_path.name}")
        return True
    except Exception as e:
        print(f"Error updating README: {e}")
        return False

def generate_default_readme(repo_path: Path) -> str:
    """
    Generate a modern README template based on gitship features.
    """
    return """# gitship 🚀

**Ship code faster, safer, and smarter.**

`gitship` is the all-in-one CLI for modern Python development workflows. It automates the tedious parts of git history, releasing, dependency management, and publishing.

## ✨ Features

### ⚓ Release Automation (`gitship release`)
Stop messing with git tags and manual changelogs.
- **Interactive Versioning**: Smart semantic version bumping (major/minor/patch).
- **Auto-Changelog**: Generates beautiful changelogs from your commit history, grouped by category.
- **GitHub Releases**: Automatically drafts releases with release notes.
- **PyPI Integration**: Checks PyPI status, sets up OIDC trusted publishing, and triggers workflows.

### 🧠 Intelligent Commits (`gitship commit`)
Write better history without trying.
- **Change Analysis**: Automatically categorizes files (Code, Docs, Tests, Config).
- **Smart Renames**: Detects renamed files even when git misses them.
- **Auto-Message**: Suggests conventional commit messages based on your changes.
- **Stats**: Detailed breakdown of lines added/removed.

### 📦 Dependency Scanner (`gitship deps`)
Never forget a `pip install` again.
- **Auto-Scan**: Scans source code for imports.
- **Smart Update**: Updates `pyproject.toml` dependencies automatically.
- **Stdlib Detection**: Distinguishes between standard library and external packages.

### 🌿 Branch Manager (`gitship branch`)
- Interactive branch creation, switching, renaming, and deletion.
- Clean up merged branches easily.

### ☁️ Instant Publishing (`gitship publish`)
- Initialize git, create private/public GitHub repositories, and push code in one step.
- Sets up PyPI Trusted Publishing (OIDC) automatically.

### 🔍 History & Inspection
- **`check`**: View recent commits and inspect file details interactively.
- **`review`**: Generate diff reports between tags or commits.
- **`fix`**: Selectively revert specific files from past commits without rolling back the whole repo.

## 🚀 Installation

```bash
pip install gitship
```

## 📖 Usage

### Interactive Mode
Just run `gitship` to enter the interactive menu:
```bash
gitship
```

### CLI Commands

**Release a new version:**
```bash
gitship release
```

**Smart commit:**
```bash
gitship commit
```

**Scan dependencies:**
```bash
gitship deps
```

**Manage branches:**
```bash
gitship branch
```

**Inspect history:**
```bash
gitship check
```

**Restore files from the past:**
```bash
gitship fix <commit-sha>
```

## ⚙️ Configuration
Manage settings via `~/.gitship/config.json` or the CLI:
```bash
gitship config --show
```

## 🤝 Contributing
Contributions are welcome!

## 📄 License
MIT
"""

def main_with_args(repo_path: Path, source: Optional[str] = None, generate: bool = False):
    """Entry point for docs command."""
    if generate:
        content = generate_default_readme(repo_path)
        dest = repo_path / "README.md"
        backup = dest.with_suffix(".md.bak")
        
        if dest.exists():
            shutil.copy(dest, backup)
            print(f"📦 Backed up existing README to {backup.name}")
        
        dest.write_text(content)
        print(f"✅ Generated new README.md with gitship features")
        return

    if source:
        update_readme(Path(source), repo_path)
    else:
        print("Usage: gitship docs --source <file> OR --generate")