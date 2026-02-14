# Gitship Branch Management - Examples

## Interactive Menu Mode

```bash
# Launch interactive branch manager
gitship branch

# Or from main menu
gitship
# Then choose option 7
```

**Interactive Menu Shows:**
```
============================================================
BRANCH MANAGEMENT
============================================================
Repository: /home/user/myproject
Current Branch: master
Default Branch: master

Local Branches:
● master (default)
  feature/new-ui
  bugfix/login-issue

Available Operations:
  1. Create new branch
  2. Switch branch
  3. Rename current branch
  4. Change default branch
  5. Delete branch
  6. List all branches (including remote)
  0. Exit
```

## CLI Mode Examples

### List Branches
```bash
# List local branches
gitship branch list

# List with remote branches
gitship branch list --show-remote
```

### Create Branch
```bash
# Create and switch to new branch
gitship branch create --name feature/awesome --switch

# Create from specific commit/tag
gitship branch create --name hotfix --from v1.2.0

# Create without switching
gitship branch create --name experiment
```

### Switch Branch
```bash
gitship branch switch --name develop
```

### Rename Branch
```bash
# Rename current branch (local only)
gitship branch rename --new-name feature/better-name

# Rename and update remote
gitship branch rename --new-name main --remote

# Rename specific branch
gitship branch rename --old-name master --new-name main --remote
```

### Change Default Branch
```bash
# Set new default branch (interactive guidance)
gitship branch set-default --name main
```

**This will:**
1. ✓ Verify branch exists locally
2. ✓ Push to remote (if needed)
3. ✓ Update local tracking
4. 💡 Show instructions for GitHub/GitLab settings

### Delete Branch
```bash
# Safe delete (must be merged)
gitship branch delete --name old-feature

# Force delete (even if unmerged)
gitship branch delete --name experiment --force
```

## Smart Features

### 1. **Visual Branch Status**
- Current branch marked with ●
- Default branch clearly labeled
- Color-coded output

### 2. **Safety Checks**
- Can't delete current branch
- Warns about unmerged changes
- Confirms destructive operations

### 3. **Remote Sync**
- Optional remote updates for renames
- Automatic cleanup of old remote branches
- Push new default branch to remote

### 4. **Helpful Guidance**
When changing default branch, shows platform-specific instructions:
```
3. Update default branch on hosting platform:
   GitHub: Settings → Branches → Default branch → Switch to 'main'
   GitLab: Settings → Repository → Default Branch → Select 'main'
   Manual: git remote set-head origin main
```

## Typical Workflows

### Master → Main Migration
```bash
# 1. Rename master to main
gitship branch rename --old-name master --new-name main --remote

# 2. Set as default
gitship branch set-default --name main

# 3. Update on GitHub/GitLab (follow instructions)
```

### Feature Branch Creation
```bash
# Quick feature branch from current state
gitship branch create --name feature/user-auth --switch
# ✓ Created branch 'feature/user-auth'
# ✓ Switched to branch 'feature/user-auth'
```

### Cleanup Old Branches
```bash
# Interactive deletion with smart warnings
gitship branch
# Choose option 5, select branches to delete
# Get warnings if unmerged
```

## Comparison: git vs gitship

**Raw Git:**
```bash
# Create and switch
git checkout -b feature/new

# Rename with remote update (multiple steps)
git branch -m old new
git push origin new
git push origin --delete old
git push origin -u new

# Change default (confusing!)
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
# ...then go to GitHub settings...

# Delete (error messages not beginner-friendly)
git branch -d feature
# error: The branch 'feature' is not fully merged.
```

**Gitship:**
```bash
# Create and switch
gitship branch create --name feature/new --switch

# Rename with remote update (single command)
gitship branch rename --new-name new --remote
# ✓ Renamed local branch 'old' → 'new'
# ✓ Pushed 'new' to remote
# ✓ Deleted 'old' from remote

# Change default (with guidance!)
gitship branch set-default --name main
# ✓ Branch pushed/updated on remote
# ✓ Updated local tracking
# 💡 Shows GitHub/GitLab instructions

# Delete (helpful feedback)
gitship branch delete --name feature
# ✗ Failed to delete branch: not fully merged
# 💡 Use force delete if you're sure (will lose unmerged changes)
```

## Why Gitship Branch is Better

1. **Single command** for complex operations (rename + remote sync)
2. **Clear visual feedback** with colors and symbols
3. **Safety warnings** that explain consequences
4. **Platform guidance** for GitHub/GitLab settings
5. **Interactive mode** for exploratory workflows
6. **Beginner-friendly** error messages
7. **Smart defaults** (like auto-detecting current branch)