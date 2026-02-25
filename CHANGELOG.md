# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] — 2026-02-25

Documentation Platform & Release Workflow Optimization

🚀 **Documentation Engineering Platform**
- **MkDocs Integration:** Added `docbuilder.py`, a robust documentation management system with collision detection, metadata migration, and dual navigation modes (manual vs. awesome-pages).
- **Deployment Suite:** Added `mkdocs_deploy.py` for one-click deployment options:
  - **Local:** Safe port finding and foreground preview.
  - **Persistent:** Auto-generated `systemd` user services to keep docs alive 24/7.
  - **GitHub Pages:** Automated workflow generation and `gh` CLI activation.

⚡ **Release & Workflow Optimization**
- **Instant Tag Picker:** Refactored `release.py` to use lazy fetching and smart defaults (`git describe`). This eliminates the massive delay caused by pre-fetching all remote tags in large repositories.
- **Resilient Sync:** Hardened `sync.py` and `resolve_conflicts.py` to detect and recover from interrupted rebases/merges gracefully.
- **Non-Blocking Git:** Switched to interactive execution (`GIT_EDITOR=true`) for merge/amend operations to prevent the CLI from hanging on background editors.

🛠️ **CLI Power Tools**
- **Tag & Stash Managers:** Added dedicated interactive menus for managing git tags and stashes.
- **PyPI Source Diff:** The release flow can now download published PyPI source distributions and diff them directly against the local working tree when no matching git tag exists.
- **Advanced Review:** Enhanced commit review with pagination, per-file diff browsing, and binary file detection.
- **Branch Health:** Added automated detection and fixing for broken upstream tracking (e.g., "[gone]" branches).

---

**📝 Code Changes:**
- UPDATE: src/gitship/branch.py (554 lines changed)
- UPDATE: src/gitship/cli.py (86 lines changed)
- UPDATE: src/gitship/commit.py (870 lines changed)
- UPDATE: src/gitship/config.py (55 lines changed)
- UPDATE: src/gitship/deps.py (81 lines changed)
- UPDATE: src/gitship/gitops.py (38 lines changed)
- UPDATE: src/gitship/merge_message.py (128 lines changed)
- UPDATE: src/gitship/pypi.py (20 lines changed)
- UPDATE: src/gitship/release.py (497 lines changed)
- UPDATE: src/gitship/resolve_conflicts.py (429 lines changed)
- UPDATE: src/gitship/review.py (390 lines changed)
- NEW: src/gitship/stash.py (264 lines changed)
- UPDATE: src/gitship/sync.py (514 lines changed)
- NEW: src/gitship/tag.py (619 lines changed)

**📚 Documentation:**
- docs/index.md (3 lines)
- mkdocs.yml (6 lines)
- src/gitship/docbuilder.py (1019 lines)
- src/gitship/docs.py (676 lines)
- src/gitship/mkdocs_deploy.py (745 lines)

**⚙️ Configuration:**
- config.json (0 lines)

**Additional Changes:**
- feat(docs): integrate MkDocs builder, deployment suite, and optimized release workflow
- feat(cli): add tag/stash managers, PyPI diffs, and adv review

_21 files changed, 5946 insertions(+), 1088 deletions(-)_

## [0.5.0] — 2026-02-21

CI Regression Debugger & Smart Workflow Engine

This release introduces a groundbreaking interactive CI regression debugger and a completely overhauled engine for branch, merge, and cherry-pick operations, making Gitship smarter, safer, and more powerful than ever.

Instantly find what broke your build with the new `gitship ci` regression debugger!

- **Find the Failure:** Automatically identifies the last successful CI run on your current branch and diffs it against HEAD.
- **Hunk-Level Revert:** Presents each changed code block (hunk) individually, allowing you to interactively revert *only* the specific lines that caused the failure.
- **Intelligent Contextual Search:** Search for error messages or log lines across all changed files. Gitship finds the line, identifies the enclosing function, and intelligently displays all related hunks, including those in caller functions.
- **Automated Fix:** Builds and applies a reverse patch for your selected hunks, staging them for a new "fix:" commit, turning hours of debugging into seconds of guided fixing.

The core branching, merging, and cherry-picking logic has been rebuilt for enhanced intelligence and safety.

- **Smart Merge:** Pre-fetches remote status and syncs the target branch *before* merging to prevent common push rejections.
- **Smart Push:** Automatically detects diverged branches and offers safe resolution options (rebase or force-with-lease) to prevent accidental history overwrites.
- **Robust Cherry-Pick:** Now handles complex scenarios like resuming stuck operations, automatically skipping merge commits, and gracefully handling sequences of empty/redundant patches. It also offers to amend the final commit with a detailed, auto-generated message.
- **Safe Branch Deletion:** A new two-step confirmation process (y/n, then type the name) warns you about unmerged commits and remote status, making accidental deletion nearly impossible.

- **Graceful Exits:** Ctrl+C now cancels operations cleanly across all interactive prompts without a messy traceback.
- **Conflict Resolver:** Now recognizes and can resume in-progress `cherry-pick` conflicts.
- **Auto-ignore:** Gitship's internal directories (`.gitship/`, `gitship_exports/`) are now automatically added to your project's `.gitignore` on first run.
- **Dependency Fix:** Correctly identifies `ruamel.yaml` during dependency scans.

---

**📝 Code Changes:**
- UPDATE: src/gitship/branch.py (1260 lines changed)
- UPDATE: src/gitship/ci.py (1238 lines changed)
- UPDATE: src/gitship/cli.py (82 lines changed)
- UPDATE: src/gitship/commit.py (47 lines changed)
- UPDATE: src/gitship/gitignore.py (40 lines changed)
- UPDATE: src/gitship/merge.py (784 lines changed)
- UPDATE: src/gitship/resolve_conflicts.py (20 lines changed)

**⚙️ Configuration:**
- pyproject.toml (2 lines)

_9 files changed, 2716 insertions(+), 763 deletions(-)_

## [0.4.0] — 2026-02-20

CI Control Plane, Smart Init & History Rescue

## 🚀 New Features

- **Interactive Dashboard**: View GitHub Actions stats, failure rates, and duration.
- **Log Inspection**: Inspect run logs with error highlighting.
- **Actions**: Trigger, rerun (failed/all), and cancel workflows via CLI.
- **Management**: Create/Edit workflows from templates using atomic writes and file locking. Manage triggers/cron schedules interactively.

- **Repo Repair**: Detects corruption via `git fsck` and attempts `git gc` recovery.
- **Blob Healing**: Integrates with `vscode-history` to recover missing blobs/files during setup.

- **Standalone Tool**: Scan and restore files from local VSCode timeline.
- **Safety**: Supports diff previews and dry-runs before restoring.

- **Smart Recommendations**: Release wizard now analyzes git history/diffs to recommend semver bumps (Major/Minor/Patch).
- **Unrelated Histories**: New wizard for merging independent trees (Rebase, Force Merge, or Push Separate PR).
- **Versioning**: Support for CVE-based versioning schemes.

## 🐛 Fixes & Improvements
- **Commit**: Defaults to `pull --rebase` before pushing to avoid upstream conflicts.
- **Release Logic**: Fixed semantic version recommendation to correctly identify breaking changes for major bumps.
- **Dependencies**: Added `filelock` and `ruamel` for robustness.

---

**📝 Code Changes:**
- UPDATE: src/gitship/branch.py (263 lines changed)
- NEW: src/gitship/ci.py (1563 lines changed)
- UPDATE: src/gitship/cli.py (557 lines changed)
- UPDATE: src/gitship/commit.py (41 lines changed)
- NEW: src/gitship/init.py (603 lines changed)
- UPDATE: src/gitship/release.py (326 lines changed)
- NEW: src/gitship/vscode_history.py (466 lines changed)

**⚙️ Configuration:**
- pyproject.toml (2 lines)

**Additional Changes:**
- fix: return major bump when breaking changes detected
- feat: add smart version bump recommendation to release wizard
- feat: add CI control plane, smart init with rescue, and advanced release workflows

_8 files changed, 3519 insertions(+), 302 deletions(-)_

## [0.3.5] — 2026-02-18

Update license to AGPL.

📝 RELEASE NOTES — gitship v0.3.5
Update License to AGPL-3

What’s New

📝 License Change

The project license has been updated from MIT to AGPL-3.0.
This ensures that all derivative work, including hosted or SaaS versions, must remain open and freely available under the same license.
	•	LICENSE file updated to AGPL-3.0
	•	Legal and compliance alignment for collaborative and server-side usage

No functional changes were made to the code — this release is purely legal and licensing.

⸻

Full Changelog: v0.3.4 → v0.3.5
	•	docs: Update license to AGPL (commit c7d4773)

---

_1 file changed, 23 insertions(+), 19 deletions(-)_

## [0.3.4] — 2026-02-18

The Self-Aware Release Update

🚀 **GITSHIP SELF-HEALING UPDATE**

This version introduces smart state detection to handle the "messy" parts of releasing. GitShip now acts as a detective to ensure your tags, GitHub releases, and PyPI packages stay in perfect sync.

* **Workflow Monitoring:** Real-time tracking of `publish.yml` via `gh` to prevent double-publishes or race conditions.
* **PyPI Verification:** Automatically checks the PyPI JSON API to verify if a deployment actually succeeded.
* **Draft Release Support:** Found a draft? GitShip can now promote it to full release or edit it in-place.
* **Tag Synchronization:** Detects when tags exist locally but are missing on the remote origin and offers an instant fix.

* **Smart Recovery Menu:** A new interactive CLI menu triggers when a partial release is detected, allowing for surgical "Reset" or "Resume" actions.
* **Changelog Optimization:** Refactored history lookups to be faster by avoiding root-commit overhead on large repositories.

---
*Generated by GitShip — The "Self-Aware" Release Manager*

---

**📝 Code Changes:**
- UPDATE: src/gitship/release.py (428 lines changed)

_1 file changed, 283 insertions(+), 145 deletions(-)_

## [0.3.3] — 2026-02-17

Hotfix: PyPI Workflow Secrets & Staged File Visibility

This release critically repairs the `publish.yml` generation logic that caused workflow validation errors in v0.3.2, and fixes core issues with how staged files are detected.

## 🐛 Critical Bug Fixes
*   **GitHub Actions Workflow:** Fixed `Unrecognized named-value: 'secrets'` error. The generated `publish.yml` no longer attempts to access `secrets.PYPI_API_TOKEN` inside `if:` conditions (where the secrets context is unavailable). It now relies on step-level logic to handle OIDC fallbacks safely.
*   **Staged File Visibility:** Fixed a logic error in `commit.py` where files staged with `git add` (Status `A ` or `M `) were invisible to the diff engine.
*   **License Detection:** Fixed "Unknown" license detection for packages like `yt-dlp` and `Flask`. The scanner now checks `pip` metadata fields before attempting to scrape files.

## 🛠 Improvements
*   **First Release Support:** Fixed an issue where generating release notes for a repository with *zero* previous tags would result in an empty diff (now correctly resolves the root commit).
*   **Commit Categorization:** Fixed a bug where files in `other/` or `tests/` categories were excluded from the detailed commit breakdown.
*   **File Type Detection:** Files without extensions (like `LICENSE` or `Makefile`) are now correctly categorized instead of falling into "Other".

---

**📝 Code Changes:**
- UPDATE: src/gitship/commit.py (480 lines changed)
- UPDATE: src/gitship/licenses.py (262 lines changed)
- UPDATE: src/gitship/pypi.py (9 lines changed)
- UPDATE: src/gitship/release.py (29 lines changed)

_4 files changed, 618 insertions(+), 162 deletions(-)_

## [0.3.2] — 2026-02-17

Fix comment stripping in release notes

- **Comment stripping in release notes editor**
  Previously, only lines starting with `# ` (hash + space) were stripped as comments, while lines starting with just `#` (like the example comments in the template) were not. This caused template examples to potentially appear in final release notes. Now any line starting with `#` is properly stripped, with markdown headings (`## ...`) preserved.

- This is a one-character fix (`"# "` → `"#"`) that makes gitship's release note generator correctly strip its own example comments. Gitship, healing itself. ✨

---

**📝 Code Changes:**
- UPDATE: src/gitship/release.py (2 lines changed)

_1 file changed, 1 insertion(+), 1 deletion(-)_

## [0.3.1] — 2026-02-17

Atomic Translation Snapshots & Conflict-Safe Commits

This release makes gitship **ship itself** — the tool now has a new atomic commit flow for translations that:

- **Freezes exactly what you reviewed** — captures a snapshot of .po/.mo diffs vs HEAD the moment you enter review mode
- **Shows frozen preview** — large patches auto-preview first 50 lines per file (or full if small), with export to ~/gitship_exports/
- **Safely commits the snapshot** — stashes AI's latest work, writes the reviewed snapshot, stages/commits, then pops stash to restore AI progress
- **Auto-resolves binary conflicts** — .mo files never 3-way merge cleanly; now detects binaries via git diff --numstat and takes --theirs (AI's latest) automatically
- **Adds description/body input** — inline typing or editor for meaningful commit context (bullets, closes issues, etc.)

All this means gitship can now **commit its own translation changes** (or any ignored files) while an AI keeps modifying them in the background — without conflicts or losing live work.

**Meta moment**: This very release was committed using the new flow — gitship shipped itself!

Other tweaks:
- commit.py: new commit_translations_only() + review loop with snapshot locking
- gitops.py: capture_file_snapshot(), atomic_commit_with_snapshot(), stash pop binary resolver
- licenses.py: better transitive dep resolution (requirements.txt first)
- release.py: cleaned editor template

Ready for real-world translation workflows — massive Arabic surge (95%+) was committed cleanly with this!

#
#
#
#

---

**📝 Code Changes:**
- UPDATE: src/gitship/commit.py (398 lines changed)
- UPDATE: src/gitship/gitops.py (275 lines changed)
- UPDATE: src/gitship/licenses.py (55 lines changed)
- UPDATE: src/gitship/release.py (33 lines changed)

_4 files changed, 716 insertions(+), 45 deletions(-)_

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
