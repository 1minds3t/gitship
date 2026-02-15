# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-02-14

First public release of gitship – the interactive Git workflow tool that makes version control actually pleasant.

**Features:**
- feat: complete v0.2.0 release with all code changes

**Configuration Updates:**
- Update 1 code files
- Update 5 code files; tests; documentation

**Other Changes:**
- docs: Add 0.2.0 to CHANGELOG
- Reset version to 0.2.0
- Rename: checkgit→check, fixgit→fix; Update 9 code files; Update tests; Update documentation; Update configuration
- test: add test file
- docs: update README
- Initial commit: gitship v0.1.0

_1 file changed, 8 insertions(+), 2 deletions(-)_

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
