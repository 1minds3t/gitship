# gitship 🚀

**Ship code faster, safer, and smarter.**

`gitship` is the Swiss Army Knife for modern Python development. It transforms the tedious parts of version control—releasing, committing, dependency management, and history inspection—into automated, interactive workflows.

[![PyPI version](https://badge.fury.io/py/gitship.svg)](https://badge.fury.io/py/gitship)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ Features

### ⚓ Release Automation (`gitship release`)
The complete release pipeline in one command.
- **Interactive Versioning**: Guided semantic version bumping (patch/minor/major).
- **Smart Changelog**: Automatically generates detailed changelogs from git history, grouping features, fixes, and updates.
- **GitHub Releases**: Creates draft releases with auto-generated notes for review.
- **PyPI Integration**: Checks PyPI status, auto-configures **Trusted Publishing (OIDC)**, and ensures workflows are present.
- **Safety First**: Stashes translation/local changes automatically during release.

### 🧠 Intelligent Commits (`gitship commit`)
Commit like a pro without the effort.
- **Change Analysis**: Scans your changes and categorizes them (Code, Docs, Tests, Config, Translations).
- **Smart Renames**: Detects renamed files based on content similarity, even when git misses them.
- **Auto-Message**: Suggests conventional commit messages based on file analysis.
- **Diff Review**: Interactive diff viewer for staged and unstaged changes.

### 📦 Dependency Scanner (`gitship deps`)
Keep your `pyproject.toml` in sync.
- **Auto-Scan**: Parses source code AST to find imports.
- **Smart Update**: Adds missing dependencies to `pyproject.toml` automatically.
- **Filtering**: Distinguishes between standard library modules and external packages.
- **Omnipkg Support**: Integrates with `omnipkg` for advanced mapping if available.

### 🌿 Branch Manager (`gitship branch`)
Git branching, simplified.
- **Interactive Management**: List, create, rename, and delete branches via menu.
- **Cleanup**: Identify and delete merged branches.
- **Default Branch**: Easily change default branch settings locally and remotely.

### ☁️ Instant Publishing (`gitship publish`)
Go from local folder to published repo in seconds.
- **One-Step Setup**: Initializes git, creates the GitHub repository (public/private), and pushes code.
- **Identity Verification**: Verifies GitHub identity before acting.

### 🔍 History & Inspection
- **`check`**: View recent commits, see file lists, and inspect changes interactively.
- **`review`**: Generate comprehensive diff reports between any two tags or commits.
- **`fix`**: Selectively restore specific files from past commits (atomic revert for single files).

## 🚀 Installation

```bash
pip install gitship
```

## 📖 Usage

### Interactive Mode
Run `gitship` without arguments to enter the interactive menu:
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

**Scan and update dependencies:**
```bash
gitship deps
```

**Manage branches:**
```bash
gitship branch
```

**Review history diffs:**
```bash
gitship review --from v0.1.0 --to HEAD
```

**Restore files from a past commit:**
```bash
gitship fix <commit-sha>
```

## ⚙️ Configuration

`gitship` stores configuration in `~/.gitship/config.json`.

View current settings:
```bash
gitship config --show
```

Set a default export path for reviews:
```bash
gitship config --set-export-path ~/code-reviews
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
```