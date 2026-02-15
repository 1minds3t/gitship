# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] — 2026-02-14

Add commit, fix test_changelog and changelog_generator

**Modified:**
- src/gitship/changelog_generator.py (+71/-7 lines)

**Additional Changes:**
- Update 2 code files; Update tests
- Update 6 code files; Update configuration

_11 files changed, 1521 insertions(+), 66 deletions(-)_

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
