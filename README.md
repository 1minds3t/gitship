# gitship

**Craft your Git history with precision and ease.**

`gitship` is a collection of interactive Git tools that make working with your repository history intuitive and safe. Perfect for developers who want to inspect, understand, and selectively revert commits without the usual command-line complexity.

## Features

### 🔍 `checkgit` - Interactive Commit Inspector
- View the last 10 commits in a beautifully formatted list
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

## Installation

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

## Usage

### checkgit

Navigate to any git repository and run:

```bash
checkgit
```

This will:
1. Show you the last 10 commits with hashes, times, and messages
2. Let you inspect detailed changes for any commit
3. Optionally revert a commit through an interactive workflow

**Example:**
```bash
cd ~/my-project
checkgit

# Output:
# === Last 10 commits in my-project (branch: main) ===
# 1       a1b2c3d - 2 hours ago - Fix bug in parser (john)
# 2       e4f5g6h - 5 hours ago - Add new feature (jane)
# ...
# 
# Show detailed file changes for any commit? (y/n): y
# Enter commit number (1-10): 1
```

### fixgit

Restore specific files from before a problematic commit:

```bash
fixgit [repo-path] [commit-sha]
```

**Example:**
```bash
# From within a repo
fixgit . a1b2c3d

# Or specify the repo path
fixgit ~/my-project a1b2c3d

# Interactive workflow:
# Files changed in commit a1b2c3d:
# 1. src/parser.py
# 2. tests/test_parser.py
# 3. README.md
# 
# Enter the number(s) of the file(s) to restore (e.g., '1' or '1 2 3', or 'all'): 1 2
```

The tool will:
1. Show all files changed in that commit
2. Let you select which ones to restore
3. Create a new commit with the restored files
4. Push the changes automatically

## Why gitship?

**Problem:** Git is powerful but intimidating. Reverting changes often requires memorizing complex commands, and it's easy to accidentally lose work or create a mess in your history.

**Solution:** `gitship` provides a safe, interactive interface for the most common history-management tasks. No more googling "how to revert a file to previous commit" or worrying about destructive operations.

## Future Features

Coming soon:
- **AI-powered commit messages**: Automatically generate detailed, structured commit messages using local or cloud LLM APIs
- **Batch commit analysis**: Process multiple commits at once with summary statistics
- **Commit history visualization**: Graph-based representation of your repository history
- **Smart batching for auto-commits**: Intelligent grouping of related changes
- **MCP integration**: Model Context Protocol support for LLM commit message generation

## Contributing

Contributions welcome! Please feel free to submit a Pull Request.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

Created by [1minds3t](https://github.com/1minds3t)

---

**Note**: `gitship` is designed for local development workflows. Always review changes before pushing to shared/production branches.# gitship
