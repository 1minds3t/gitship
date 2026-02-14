# gitship

**Craft your Git history with precision and ease.**

`gitship` is a collection of interactive Git tools that make working with your repository history intuitive and safe. Perfect for developers who want to inspect, understand, and selectively revert commits without the usual command-line complexity.

## ✨ Features

### 🎛️ `gitship` - Main CLI Interface
- **Interactive menu** for easy navigation
- **Argparse-based commands** for automation and scripting
- **Multiple workflows**: menu-driven or direct CLI commands
- **Configuration management** with persistent settings

### 🔍 `checkgit` - Interactive Commit Inspector
- View the last N commits (default 10, configurable)
- See detailed file changes for any commit
- Interactive revert workflow with confirmation prompts
- Automatic logging of all operations
- Auto-detects current repository

### 🔧 `fixgit` - Selective File Restorer
- Restore specific files to their state before a problematic commit
- Choose exactly which files to revert from a numbered list
- Select all files or pick individual ones
- Safe, atomic operations with automatic cleanup
- Integrates with your existing git workflow

### 📊 `reviewgit` - Comprehensive Diff Analyzer (NEW!)
- **Show diff statistics** between any two tags, commits, or branches
- **Display all commit messages** with full descriptions between references
- **Export comprehensive diffs** to structured text files
- **Configurable export path** with historical metadata
- **Individual commit statistics** - see changes for each commit
- **Interactive workflow** - choose what to view/export
- **Structured file naming** - easy to find historical reviews

## 🚀 Installation

### From PyPI (recommended)
```bash
pip install gitship
```

### From source
```bash
git clone https://github.com/1minds3t/gitship.git
cd gitship
pip install -e .
```

### Quick setup script
```bash
cd ~/gitship
chmod +x setup_gitship.sh
./setup_gitship.sh
```

## 📖 Usage

### Main CLI Interface

```bash
# Interactive menu
gitship

# Show help
gitship --help

# Run specific command
gitship checkgit
gitship fixgit a1b2c3d
gitship reviewgit

# Specify repository
gitship -r ~/myproject checkgit
```

### checkgit

View and inspect recent commits:

```bash
# View last 10 commits (default)
checkgit

# View last 20 commits
gitship checkgit -n 20

# Or use the standalone command
cd ~/my-project
checkgit
```

**Example output:**
```
=== Last 10 commits in my-project (branch: main) ===

1       a1b2c3d - 2 hours ago - Fix bug in parser (john)
2       e4f5g6h - 5 hours ago - Add new feature (jane)
...

Show detailed file changes for any commit? (y/n): y
Enter commit number (1-10): 1
```

### fixgit

Restore specific files from before a problematic commit:

```bash
# Interactive mode
fixgit

# Direct mode with commit SHA
fixgit a1b2c3d

# Via main CLI
gitship fixgit a1b2c3d
```

**Example workflow:**
```
Files changed in commit a1b2c3d:
1. src/parser.py
2. tests/test_parser.py
3. README.md

Enter the number(s) of the file(s) to restore (e.g., '1' or '1 2 3', or 'all'): 1 2
```

### reviewgit (NEW!)

Comprehensive diff analysis between tags or commits:

```bash
# Review changes from last tag to HEAD
reviewgit

# Review between specific tags
gitship reviewgit --from v1.0.0 --to v2.0.0

# Export full diff to file
gitship reviewgit --export

# Custom export path
gitship reviewgit --export --export-path ~/my-reviews

# Just show statistics
gitship reviewgit --stat-only
```

**Interactive workflow:**
```
=== GITSHIP REVIEW: myproject (branch: main) ===
From: v1.0.0
To: HEAD
================================================================================

DIFF STATISTICS
================================================================================
 src/main.py    | 45 ++++++++++++++++++++++++++++++++++
 README.md      | 12 +++++----
 2 files changed, 52 insertions(+), 5 deletions(-)

Total commits: 15

What would you like to review?
  1. Show all commit messages (with descriptions)
  2. Show individual commit statistics
  3. Show both messages and statistics
  4. Export everything to file
  0. Skip and exit

Enter your choice (0-4):
```

**Export file structure:**
```
================================================================================
GIT DIFF EXPORT: myproject
================================================================================
From: v1.0.0
To: HEAD
Generated: 2026-02-12 21:00:00
Total commits: 15
================================================================================

SUMMARY STATISTICS
[diff --stat output]

COMMIT HISTORY
[All commits with full messages and individual stats]

FULL DIFF
[Complete diff content]
```

### Configuration

Manage gitship settings:

```bash
# View current configuration
gitship config --show

# Set default export path
gitship config --set-export-path ~/omnipkg_git_cleanup

# Configuration file location: ~/.gitship/config.json
```

**Default configuration:**
```json
{
  "export_path": "~/omnipkg_git_cleanup",
  "auto_push": true,
  "default_commit_count": 10
}
```

## 📂 File Organization

### Export Structure

Diff exports are saved with structured filenames:
```
<repo_name>_diff_<from_ref>_to_<to_ref>_<timestamp>.txt

Example:
myproject_diff_v1.0.0_to_HEAD_20260212_210530.txt
```

This makes it easy to:
- Track historical reviews
- Compare different diff ranges
- Find specific reviews by timestamp
- Organize in version control

## 🎯 Why gitship?

**Problem:** Git is powerful but intimidating. Reviewing changes between releases, reverting commits, or understanding repository history often requires memorizing complex commands, and it's easy to accidentally lose work or create a mess.

**Solution:** `gitship` provides a safe, interactive interface for the most common history-management tasks with:
- **No googling required** - intuitive menus and prompts
- **Safe operations** - confirmations before destructive changes
- **Comprehensive reviews** - see all changes between any two points
- **Historical exports** - keep records of what changed when
- **Configurable workflows** - adapt to your needs

## 🔮 Use Cases

### Release Management
```bash
# Review all changes since last release
gitship reviewgit --from v1.5.0 --to HEAD --export

# Export for release notes
gitship reviewgit --from v1.5.0 --to v2.0.0 --export-path ~/releases
```

### Code Review
```bash
# Review PR changes
gitship reviewgit --from main --to feature-branch

# See individual commit details
gitship checkgit -n 20
```

### Debugging
```bash
# Find when a bug was introduced
gitship checkgit

# Revert specific files
gitship fixgit <commit-sha>
```

### Documentation
```bash
# Export complete changelog between versions
gitship reviewgit --from v1.0.0 --to v2.0.0 --export

# Keep historical records
ls ~/omnipkg_git_cleanup/myproject_diff_*
```

## 🛠️ Advanced Features

### Multiple Repository Support
```bash
# Work with different repos
gitship -r ~/project1 checkgit
gitship -r ~/project2 reviewgit
```

### Automation
```bash
# Script releases
for tag in $(git tag | tail -n 5); do
    gitship reviewgit --from $tag --to HEAD --export
done
```

### Custom Export Paths
```bash
# Per-project export locations
gitship config --set-export-path ~/code-reviews/myproject
```

## 🧪 Testing

Run the test suite:

```bash
pytest tests/ -v
```

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📝 Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## 🔒 License

MIT License - see [LICENSE](LICENSE) for details.

## 👤 Author

Created by [1minds3t](https://github.com/1minds3t)

---

## 🚧 Future Features

Coming soon:
- **AI-powered commit messages**: Automatically generate detailed, structured commit messages
- **Batch commit analysis**: Process multiple commits at once with summary statistics
- **Commit history visualization**: Graph-based representation of your repository history
- **Smart batching for auto-commits**: Intelligent grouping of related changes
- **MCP integration**: Model Context Protocol support for LLM commit message generation
- **Diff comparison**: Compare diffs across different time periods
- **Interactive merge conflict resolution**: Guided conflict resolution
- **Multi-repository management**: Handle multiple repos in one interface

---

**Note**: `gitship` is designed for local development workflows. Always review changes before pushing to shared/production branches.