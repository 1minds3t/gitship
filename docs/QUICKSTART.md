# Gitship Quick Start Guide

## Installation

```bash
cd ~/gitship
./setup_gitship.sh
```

Or manually:
```bash
pip install -e .
```

## First Time Setup

1. **Configure export path** (optional):
```bash
gitship config --set-export-path ~/omnipkg_git_cleanup
```

2. **Test the installation**:
```bash
gitship --help
checkgit --help
```

## Common Workflows

### 1. Quick Commit Review
```bash
# See last 10 commits
checkgit

# See last 20 commits  
gitship checkgit -n 20
```

### 2. Revert a File
```bash
# Interactive
fixgit

# Direct (if you know the commit SHA)
fixgit a1b2c3d
```

### 3. Review Changes for Release

**Between HEAD and last tag:**
```bash
reviewgit
# Choose option 4 to export everything
```

**Between specific tags:**
```bash
gitship reviewgit --from v1.0.0 --to v2.0.0 --export
```

**Export location:**
- Default: `~/omnipkg_git_cleanup/`
- Files named: `<repo>_diff_<from>_to_<to>_<timestamp>.txt`

### 4. Interactive Menu
```bash
# Just run gitship with no arguments
gitship
```

## Tips

### Reviewing Multiple Releases
```bash
# Export all releases
for tag in $(git tag | tail -n 5); do
    gitship reviewgit --from $tag --to HEAD --export
done
```

### Working with Multiple Repos
```bash
# Use -r flag
gitship -r ~/project1 checkgit
gitship -r ~/project2 reviewgit --export
```

### Configuration File
Edit `~/.gitship/config.json`:
```json
{
  "export_path": "/home/user/omnipkg_git_cleanup",
  "auto_push": true,
  "default_commit_count": 10
}
```

## GitHub Setup

### Create Remote Repository
1. Go to https://github.com/new
2. Create repository named `gitship`
3. Run:

```bash
cd ~/gitship
git remote add origin https://github.com/1minds3t/gitship.git
git push -u origin main --tags
```

### Keep It Updated
```bash
git add .
git commit -m "feat: your changes"
git push
```

## Troubleshooting

### Command not found
```bash
# Reinstall in development mode
pip install -e .
```

### Git repository errors
```bash
# Make sure you're in a git repo
git status

# Or specify repo explicitly
gitship -r /path/to/repo checkgit
```

### Export path doesn't exist
```bash
# Set a valid path
gitship config --set-export-path ~/my-exports
```

## Next Steps

1. Read the full [README.md](README.md)
2. Check [CONTRIBUTING.md](CONTRIBUTING.md) for development
3. See [CHANGELOG.md](CHANGELOG.md) for version history