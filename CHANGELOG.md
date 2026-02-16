# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-02-15

The "Atomic Workflow" Update - Interactive Operations & AI-Ready Foundation

## 🚀 Major Feature Release: The "Atomic Workflow" Engine

This release transforms Gitship from a collection of scripts into a robust Git orchestration platform with intelligent automation and safety guarantees.

### 🌟 Key Highlights

**Atomic GitOps Engine:** All operations now use a safety layer that automatically stashes and restores ignorable files (translations, configs, build artifacts) to prevent "dirty tree" errors during context switches.

**Interactive Commit & Release Builders:** Both workflows now feature 3-step interactive builders:
- Conventional commit type selection (feat/fix/docs/etc)
- Custom title with smart suggestions
- Editor for detailed notes with auto-generated breakdown appended
- Full markdown support with preserved headers

**Pre-Release Review:** Before generating changelogs, see a full commit review with descriptions and statistics - helps write better release notes and prevents blind releases.

**PyPI Version Sync:** Release workflow now compares against PyPI's latest version (not just local git tags) to ensure accurate changelog ranges.

### 🛠 New Features

**License Compliance Manager:**
- Auto-fetch dependency licenses from PyPI with GitHub fallback
- Generate `THIRD_PARTY_NOTICES.txt` and `MANIFEST.in`
- Auto-sync `[tool.setuptools]` license-files in `pyproject.toml`
- Selective optional dependency group fetching
- Project license generator (MIT, Apache-2.0, BSD-3-Clause, AGPL-3.0)

**Interactive Merge Suite:**
- Guided conflict resolution with state caching
- Pause and resume complex merges without losing progress
- Semantic merge messages with categorized file statistics

**Documentation Tools:**
- Interactive README.md section editor (parse, edit, reorder, remove)
- Safe editing with automatic `.bak` creation
- Programmatic `.gitignore` management

**Smart Sync Command:**
- Unified `gitship sync`: Pull (rebase) + Push + Stale remote pruning in one flow
- Atomic operations prevent translation file conflicts

### 📈 Improvements

- Condensed diff exports with `--unified=1` (up to 70% smaller files)
- Semantic commit/merge messages with detailed file categorization
- Dependency scanner with project-specific ignore lists
- Branch management with redundant branch detection & bulk cleanup

### 🔧 New Commands

- `gitship merge` / `resolve` - Interactive merge and conflict resolution
- `gitship sync` - Unified sync workflow
- `gitship licenses` - Fetch and manage dependency licenses
- `gitship ignore` - Programmatic `.gitignore` management
- `gitship docs --edit` - Interactive documentation editor

### 🎯 Foundation for AI Integration

This release establishes the groundwork for AI-assisted workflows:
- Structured commit/review data for LLM consumption
- Non-interactive mode scaffolding for automation
- JSON-ready outputs for AI processing

#
#
## 🚀 Major Feature Release: The "Atomic Workflow" Engine
This release transforms Gitship from a collection of scripts into a robust Git orchestration platform.

### 🌟 Key Highlights
- **Atomic GitOps Engine:** All operations now use a safety layer...
- **Interactive Merge Suite:** A new guided conflict resolution workflow...

### 🛠 New Commands
- `gitship merge` / `resolve`: Interactive merge and conflict resolution
- `gitship sync`: Unified sync workflow

---

**📝 Code Changes:**
- UPDATE: src/gitship/cli.py (284 lines changed)
- UPDATE: src/gitship/commit.py (647 lines changed)
- NEW: src/gitship/gitignore.py (259 lines changed)
- NEW: src/gitship/licenses.py (1200 lines changed)
- UPDATE: src/gitship/release.py (434 lines changed)
- UPDATE: src/gitship/review.py (31 lines changed)

**📚 Documentation:**
- README.md (210 lines)
- THIRD_PARTY_NOTICES.txt (35 lines)
- requirements.txt (20 lines)
- src/gitship/docs.py (702 lines)

**⚙️ Configuration:**
- pyproject.toml (4 lines)

**Additional Changes:**
- fix: This will now properly display the commit review before generating the changelog.
- feat: integrate pre-release review and PyPI version sync
- fix: preserve markdown headers in release notes and show preview before editor
- feat: enhance release workflow with interactive notes builder
- feat: add license manager, interactive docs editor, and enhanced commit workflow

_26 files changed, 8307 insertions(+), 596 deletions(-)_

## [0.2.5] — 2026-02-14

Fix README.md, cli, and docs

**📝 Code Changes:**
- UPDATE: src/gitship/cli.py (36 lines changed)
- UPDATE: src/gitship/pypi.py (168 lines changed)
- UPDATE: src/gitship/release.py (49 lines changed)

**📚 Documentation:**
- README.md (371 lines)
- src/gitship/docs.py (162 lines)

**Additional Changes:**
- Update 2 code files
- Update 3 code files; Update documentation

_7 files changed, 411 insertions(+), 391 deletions(-)_

## [0.2.4] — 2026-02-14

Fix pypi and release

**📝 Code Changes:**
- UPDATE: src/gitship/pypi.py (13 lines changed)
- UPDATE: src/gitship/release.py (16 lines changed)

_2 files changed, 21 insertions(+), 8 deletions(-)_

## [0.2.3] — 2026-02-14

Fix commit, deps, and release

**📝 Code Changes:**
- UPDATE: src/gitship/commit.py (9 lines changed)
- UPDATE: src/gitship/deps.py (63 lines changed)
- UPDATE: src/gitship/release.py (8 lines changed)

_3 files changed, 59 insertions(+), 21 deletions(-)_

## [0.2.2] — 2026-02-14

Fix changelog_generator, cli, and commit

**📝 Code Changes:**
- NEW: src/gitship/changelog_generator.py (496 lines changed)
- UPDATE: src/gitship/cli.py (84 lines changed)
- UPDATE: src/gitship/commit.py (38 lines changed)
- NEW: src/gitship/deps.py (300 lines changed)
- UPDATE: src/gitship/pypi.py (116 lines changed)
- UPDATE: src/gitship/release.py (133 lines changed)

**🧪 Tests:**
- NEW: tests/test.py (212 lines)
- NEW: tests/test_changelog.py (173 lines)
- NEW: tests/test_changelog_extraction.py (212 lines)

**⚙️ Configuration:**
- pyproject.toml (10 lines)
- yproject.toml

**Additional Changes:**
- Update 1 code files
- Update 1 code files; Update configuration
- Update 2 code files
- Update 2 code files; Update tests
- Update 6 code files; Update configuration

_11 files changed, 1711 insertions(+), 97 deletions(-)_

## [0.2.1] — 2026-02-14

complete v0.2.1 release with all code changes

**Features:**
- feat: complete v0.2.1 release with all code changes
- feat: complete v0.2.2 release with all code changes

**Configuration Updates:**
- Update 2 code files; configuration

_4 files changed, 107 insertions(+), 82 deletions(-)_

## [0.2.0] — 2026-02-14

First public release – gitship is now live on GitHub and (soon) PyPI!

gitship is the interactive Git workflow tool that turns version control from frustrating archaeology into a guided, intelligent, and frustration-free experience.

- **Intelligent rename detection** — finds renames by content similarity *before* staging (smarter than git's default)

- **Semantic change grouping** — auto-categorizes files (code / tests / docs / config / other) for better understanding

- **Guided commit flows** — interactive review, smart message suggestions, one-command commit preparation

- **Atomic release recovery** — detects incomplete tags/releases (tag exists but code not committed), offers safe rollback + re-tag

- **Secure PyPI publishing setup** — auto-generates OIDC workflow, guides trusted publisher config, no API tokens needed

- **Identity-verified push** — prevents pushing as wrong user

- **Self-dogfooding milestone** — this release was created, recovered, tagged, and prepared for PyPI **using gitship itself**

- Added full release lifecycle automation (bump → changelog → tag → push → publish prep)

- Implemented atomic stash/pop for safe background operations (e.g. pull/rebase during release)

- Introduced proactive state checks (tag vs commit mismatch detection)

- Built-in PyPI first-release guidance + workflow generation

```bash

pip install gitship

pipx install gitship

```

https://github.com/1minds3t/gitship

What git should have been: **guided, frustration-free, and powerful**._

## [Unreleased]

### Planned
- AI-powered commit message generation using local/cloud LLMs
- Batch commit analysis and statistics
- Smart auto-commit batching for related changes
- MCP (Model Context Protocol) integration
- Commit history visualization
- Interactive merge conflict resolution
- Multi-repository management
- Configuration file support (~/.gitshiprc)
- Windows and macOS testing and support improvements

## [0.1.0] - 2026-02-12

### Added
- Initial release of gitship
- `checkgit` command for interactive commit inspection
  - View last 10 commits with formatted output
  - Inspect detailed file changes for any commit
  - Interactive revert workflow with confirmations
  - Automatic operation logging
  - Auto-detection of current repository
- `fixgit` command for selective file restoration
  - Restore specific files to state before a commit
  - Interactive file selection (individual or all)
  - Automatic commit creation with descriptive messages
  - Auto-push to remote repository
  - Autopush service integration (Linux)
- Comprehensive documentation
  - README with usage examples
  - Quick start guide
  - Testing guide
  - Contributing guidelines
- MIT License
- Python 3.8+ support
- Cross-platform compatibility (Linux, macOS, Windows)
- Proper package structure with pyproject.toml
- Basic test suite
- Logging to /var/log or /tmp

### Technical Details
- Converted from bash scripts to Python for better cross-platform support
- Uses subprocess for git command execution
- Implements proper error handling and validation
- Follows modern Python packaging standards (PEP 621)

[unreleased]: https://github.com/1minds3t/gitship/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/1minds3t/gitship/releases/tag/v0.1.0
