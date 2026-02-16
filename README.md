<div align="center">

# Gitship 🚀

### **Git on Autopilot. Stop plumbing, start shipping.**

`gitship` is a high-level workflow manager that wraps Git in a layer of intelligence and safety. It doesn't just run Git commands; it orchestrates your entire development lifecycle—from the first line of code to the final PyPI release.

[![PyPI version](https://badge.fury.io/py/gitship.svg)](https://badge.fury.io/py/gitship)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

</div>

---

## 💡 Why Gitship?

Most developers treat Git as a plumbing tool. **Gitship treats Git as an architect.**

*   **Atomic Operations:** Never see a "dirty tree" error again. Gitship automatically stashes and restores background noise (like translation files or build artifacts) during branch switches and merges.
*   **Semantic History:** Your git log shouldn't be a mystery. Gitship generates data-driven commit and merge messages that categorize changes into Features, Fixes, and Stats.
*   **Safety-First Workflows:** Rebase-by-default syncing, interactive conflict resolution with state-caching, and identity-verified publishing.

---

## 🛠 Features

### 🛡️ Atomic GitOps (The "Safe-State" Engine)
Gitship uses a unique **Atomic Engine** to ensure your repository stays clean:
- **Intelligent Stashing:** Automatically stashes and restores ignorable background changes (like AI-generated translations or config updates) during critical operations.
- **Conflict Caching:** If a merge fails, Gitship caches your resolutions, allowing you to abort, fix, and resume without losing work.

### 🧠 Intelligent Commits & Amends
- **Category Awareness:** Changes are analyzed and grouped (Code, Translations, Tests, etc.).
- **Smart Amending:** Rewrite your last commit message with automated analysis of what actually changed.
- **Rename Detection:** Content-based similarity detection even when standard Git fails to see a move.
- **Condensed Exports:** Export diffs with 60-70% size reduction using `--unified=1` for easier code review.

### 🌿 Advanced Branch & Sync
- **Unified Sync:** `gitship sync` performs a safe pull (rebase) and push in one atomic operation.
- **Directional Review:** Compare any two branches with a visual "Incoming Changes" vs "Target Status" report.
- **Bulk Cleanup:** Identify and delete redundant, merged, or stale remote branches in seconds.
- **Interactive Merging:** Guided merge workflows with conflict resolution caching.

### 📦 Dependency & Project Management
- **AST-Based Scanner:** Detects imports in your source code and maps them to PyPI packages.
- **Permanent Ignores:** Maintain a project-specific list of packages you never want to track in `pyproject.toml`.
- **README Editor:** Section-by-section interactive README editor with auto-centering for badges.
- **Gitignore Manager:** Add/remove patterns from `.gitignore` via CLI with common language templates.

### ⚓ Professional Releases
- **Semantic Versioning:** Guided patch/minor/major bumping.
- **OIDC / Trusted Publishing:** Automated PyPI release configuration.
- **Draft GitHub Releases:** Auto-generates high-quality release notes from categorized commit history.

---

## 🚀 Quick Start

### Installation
```bash
pip install gitship
```

### The "Daily Flow" Commands

| Command | Action |
| :--- | :--- |
| `gitship` | **The Dashboard:** Interactive menu for all operations. |
| `gitship sync` | Pull (rebase), resolve conflicts, and push in one go. |
| `gitship commit` | Analyze changes and commit with a smart message. |
| `gitship branch` | Manage, compare, and merge branches safely. |
| `gitship deps` | Sync `pyproject.toml` with your actual imports. |
| `gitship release` | Bump version, generate changelog, and ship to PyPI/GitHub. |
| `gitship amend` | Smart commit message rewriting with merge analysis. |
| `gitship ignore` | Manage `.gitignore` entries from CLI. |
| `gitship docs` | Interactive section-by-section README editor. |
| `gitship resolve` | Interactive conflict resolver with block-by-block choices. |

---

## 🔧 Advanced Usage

### Interactive Conflict Resolution
If a merge or pull hits a conflict, Gitship enters **Resolve Mode**:
```bash
gitship resolve
```
It provides a block-by-block interactive UI, allowing you to choose "Ours", "Theirs", or "Manual" for every conflict hunk, and caches your progress if you need to step away.

### Condensed Code Reviews
Export a massive diff into a readable, minimal-context review file:
```bash
gitship commit  # Choose option 2 to review code changes
                # Then option 4 to export diff
                # Select condensed format (60-70% smaller)
```
Uses `--unified=1` and strips noise to reduce diff size dramatically.

### Atomic Operations Under the Hood
Gitship's `gitops.py` module wraps critical Git operations (push, pull, merge, checkout) with automatic stashing/restoring:
```python
from gitship.gitops import atomic_git_operation

# Automatically handles background file changes
atomic_git_operation(
    repo_path=repo,
    git_command=["push", "origin", "main"],
    description="push to origin/main"
)
```

---

## 📚 Documentation

Full documentation and guides coming soon. For now, explore interactively:
```bash
gitship  # Main menu with all features
```

---

## 🤝 Contributing

Gitship is built by developers who are tired of Git overhead. If you have an idea to make Git "just work," we want your PRs!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`gitship commit` 😉)
4. Push to the branch (`gitship push`)
5. Open a Pull Request

---

## 📄 License

MIT License [1minds3t](https://github.com/1minds3t)

See [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

Built with:
- Python 3.8+
- [Rich](https://github.com/Textualize/rich) for beautiful terminal output
- [Typer](https://github.com/tiangolo/typer) for CLI magic
- [Omnipkg](https://github.com/1minds3t/omnipkg) for advanced dependency resolution (optional)

---

<div align="center">

**Stop fighting Git. Start shipping code.**

[Install Now](https://pypi.org/project/gitship/) • [Report Bug](https://github.com/1minds3t/gitship/issues) • [Request Feature](https://github.com/1minds3t/gitship/issues)

</div>