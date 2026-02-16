#!/usr/bin/env python3
"""
Merge module - Interactive branch merging with conflict handling
"""

import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

try:
    from gitship.gitops import atomic_git_operation, has_ignored_changes
    from gitship.merge_message import generate_merge_message, amend_last_commit_message
except ImportError:
    # Fallback if gitops not available yet
    atomic_git_operation = None
    has_ignored_changes = None
    generate_merge_message = None
    amend_last_commit_message = None


def get_merge_cache_dir(repo_path: Path) -> Path:
    """Get the merge cache directory for saving resolved conflicts."""
    cache_dir = repo_path / ".gitship" / "merge-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def save_merge_state(repo_path: Path, source_branch: str, target_branch: str):
    """Save current merge state including resolved files."""
    cache_dir = get_merge_cache_dir(repo_path)
    
    # Save merge metadata
    meta_file = cache_dir / "merge-meta.txt"
    with open(meta_file, 'w') as f:
        f.write(f"source={source_branch}\n")
        f.write(f"target={target_branch}\n")
    
    # Get all staged files (these are our resolutions)
    result = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    staged_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    
    # Save each staged file to cache
    files_saved = []
    for filepath in staged_files:
        src = repo_path / filepath
        if src.exists():
            dst = cache_dir / filepath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files_saved.append(filepath)
    
    # Save list of resolved files
    if files_saved:
        files_list = cache_dir / "resolved-files.txt"
        with open(files_list, 'w') as f:
            f.write('\n'.join(files_saved))
        
        print(f"\n💾 Saved {len(files_saved)} resolved files to merge cache")
    
    return files_saved


def load_merge_state(repo_path: Path) -> Optional[dict]:
    """Load saved merge state if it exists."""
    cache_dir = get_merge_cache_dir(repo_path)
    meta_file = cache_dir / "merge-meta.txt"
    
    if not meta_file.exists():
        return None
    
    # Read metadata
    state = {}
    with open(meta_file, 'r') as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                state[key] = value
    
    # Get list of resolved files
    files_list = cache_dir / "resolved-files.txt"
    if files_list.exists():
        with open(files_list, 'r') as f:
            state['resolved_files'] = [line.strip() for line in f if line.strip()]
    else:
        state['resolved_files'] = []
    
    return state


def restore_merge_state(repo_path: Path) -> bool:
    """Restore saved merge resolutions from cache."""
    cache_dir = get_merge_cache_dir(repo_path)
    state = load_merge_state(repo_path)
    
    if not state or not state.get('resolved_files'):
        return False
    
    print(f"\n♻️  Found cached merge resolutions from previous attempt")
    print(f"   Source: {state.get('source')} → Target: {state.get('target')}")
    print(f"   Files: {len(state['resolved_files'])} resolved")
    
    choice = input("\nRestore cached resolutions? (y/n): ").strip().lower()
    
    if choice != 'y':
        return False
    
    # Restore each cached file
    restored = 0
    for filepath in state['resolved_files']:
        cached = cache_dir / filepath
        target = repo_path / filepath
        
        if cached.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, target)
            
            # Stage the file
            run_git(["add", filepath], cwd=repo_path)
            restored += 1
    
    if restored > 0:
        print(f"✅ Restored {restored} resolved files")
        print("💡 Run 'gitship merge' again to continue")
        return True
    
    return False


def clear_merge_cache(repo_path: Path, verbose: bool = False):
    """Clear the merge cache after successful merge."""
    cache_dir = get_merge_cache_dir(repo_path)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        if verbose:
            print("🧹 Cleared merge cache")

def run_git(args, cwd=None, check=True):
    """Run git command and return result."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=check,
            cwd=cwd,
            encoding='utf-8',
            errors='replace'
        )
        return res
    except subprocess.CalledProcessError as e:
        if not check:
            return e
        print(f"Git error: {e.stderr}")
        sys.exit(1)

def get_current_branch(repo_path: Path) -> str:
    """Get the currently checked out branch."""
    result = run_git(["branch", "--show-current"], cwd=repo_path)
    return result.stdout.strip()

def get_branches(repo_path: Path, include_remote=False) -> list:
    """Get list of branches."""
    if include_remote:
        result = run_git(["branch", "-a"], cwd=repo_path)
    else:
        result = run_git(["branch"], cwd=repo_path)
    
    branches = []
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if line.startswith('*'):
            line = line[1:].strip()
        if line and not line.startswith('remotes/origin/HEAD'):
            branches.append(line.replace('remotes/origin/', ''))
    
    return sorted(set(branches))

def has_uncommitted_changes(repo_path: Path) -> bool:
    """Check if there are uncommitted changes."""
    result = run_git(["status", "--porcelain"], cwd=repo_path)
    return bool(result.stdout.strip())

def merge_branch(repo_path: Path, source_branch: str, strategy: Optional[str] = None, interactive: bool = True) -> bool:
    """Merge a branch into current branch."""
    current = get_current_branch(repo_path)
    
    print(f"\n🔀 Merging '{source_branch}' into '{current}'...")
    
    # Build merge command
    cmd = ["merge"]
    if strategy:
        cmd.extend(["-X", strategy])
    cmd.append(source_branch)
    
    # Use atomic operation if available to handle ignorable changes
    if atomic_git_operation:
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=cmd,
            description=f"merge {source_branch}"
        )
    else:
        result = run_git(cmd, cwd=repo_path, check=False)
    
    if result.returncode == 0:
        print(f"✅ Successfully merged '{source_branch}' into '{current}'")
        
        # Generate detailed merge message if generator available
        if generate_merge_message and amend_last_commit_message:
            print("\n📊 Generating detailed merge message...")
            detailed_message = generate_merge_message(repo_path, source_branch, current)
            
            print("\n" + "="*70)
            print("PROPOSED MERGE COMMIT MESSAGE:")
            print("="*70)
            print(detailed_message)
            print("="*70)
            
            choice = input("\nUse this detailed message? (y/n/e to edit): ").strip().lower()
            
            if choice == 'y':
                if amend_last_commit_message(repo_path, detailed_message):
                    print("✅ Commit message updated with detailed analysis")
                else:
                    print("⚠️  Could not amend message, keeping original")
            elif choice == 'e':
                print("\n💡 Opening editor to customize message...")
                print("    The detailed message has been copied to your clipboard (if possible)")
                # Try to copy to clipboard
                try:
                    import pyperclip
                    pyperclip.copy(detailed_message)
                    print("    ✓ Copied to clipboard")
                except:
                    pass
                
                # Open git commit --amend in editor
                subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
                print("✅ Message amended via editor")
            else:
                print("Keeping original merge message")
        else:
            print("\n📝 Original commit message:")
            print(result.stdout)
        
        return True
    else:
        # Check if it's a conflict
        if "CONFLICT" in result.stdout or "conflict" in result.stderr.lower():
            print(f"\n⚠️  Merge has conflicts!")
            print(result.stdout)
            
            if interactive:
                print("\nWhat would you like to do?")
                print("  1. Resolve interactively NOW")
                print("  2. Resolve manually later")
                print("  3. Abort merge")
                
                choice = input("\nChoice (1-3): ").strip()
                
                if choice == '1':
                    # Launch interactive resolver
                    print("\n🔧 Launching interactive conflict resolver...")
                    try:
                        from gitship import resolve_conflicts
                        resolve_conflicts.main()
                        
                        # After resolving, save state and try to commit
                        save_merge_state(repo_path, source_branch, current)
                        
                        # Check if all conflicts resolved
                        check_result = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path)
                        if check_result.stdout.strip():
                            print("\n⚠️  Some conflicts still remain")
                            print("Run 'gitship merge' again to continue")
                            return False
                        
                        # All resolved, commit
                        print("\n✅ All conflicts resolved! Committing merge...")
                        if atomic_git_operation:
                            commit_result = atomic_git_operation(
                                repo_path=repo_path,
                                git_command=["commit", "--no-edit"],
                                description="merge commit"
                            )
                        else:
                            commit_result = run_git(["commit", "--no-edit"], cwd=repo_path, check=False)
                        
                        if commit_result.returncode == 0:
                            print(f"✅ Merge committed successfully!")
                            clear_merge_cache(repo_path)
                            return True
                        else:
                            print(f"⚠️  Commit had issues: {commit_result.stderr}")
                            return False
                            
                    except Exception as e:
                        print(f"Error launching resolver: {e}")
                        print("\nYou can:")
                        print("  - Run 'gitship resolve' manually")
                        print("  - Run 'git merge --abort' to cancel")
                        return False
                
                elif choice == '3':
                    run_git(["merge", "--abort"], cwd=repo_path)
                    print("✓ Merge aborted")
                    return False
                else:
                    print("\n💡 To resolve: gitship merge (or gitship resolve)")
                    print("💡 To abort: git merge --abort")
                    save_merge_state(repo_path, source_branch, current)
                    return False
            else:
                print("\nYou can:")
                print("  1. Run 'gitship resolve' to resolve conflicts interactively")
                print("  2. Manually resolve and run 'git commit'")
                print("  3. Run 'git merge --abort' to cancel the merge")
                return False
        else:
            print(f"❌ Merge failed: {result.stderr}")
            return False


def is_merge_in_progress(repo_path: Path) -> bool:
    """Check if there's a merge in progress."""
    merge_head = repo_path / ".git" / "MERGE_HEAD"
    return merge_head.exists()


def get_merge_branch(repo_path: Path) -> Optional[str]:
    """Get the branch being merged if merge is in progress."""
    merge_msg = repo_path / ".git" / "MERGE_MSG"
    if merge_msg.exists():
        with open(merge_msg, 'r') as f:
            first_line = f.readline().strip()
            # Format: "Merge branch 'main' into development"
            if "Merge branch" in first_line:
                parts = first_line.split("'")
                if len(parts) >= 2:
                    return parts[1]
    return None


def main_with_repo(repo_path: Path):
    """Interactive merge workflow."""
    
    # FIRST: Check if we're already in a merge
    if is_merge_in_progress(repo_path):
        source_branch_name = get_merge_branch(repo_path)
        current = get_current_branch(repo_path)
        
        print(f"\n⚠️  Merge already in progress: '{source_branch_name}' → '{current}'")
        
        # Check if there are conflicts
        result = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_path)
        has_conflicts = bool(result.stdout.strip())
        
        if has_conflicts:
            print(f"\n❌ There are unresolved conflicts:")
            print(result.stdout)
            print("\nWhat would you like to do?")
            print("  1. Resolve conflicts interactively (gitship resolve)")
            print("  2. Save current state and abort")
            print("  3. Just abort (lose resolutions)")
            print("  4. Show merge status")
            
            choice = input("\nChoice (1-4): ").strip()
            
            if choice == '1':
                # Launch interactive resolver
                print("\n🔧 Launching interactive conflict resolver...")
                from gitship import resolve_conflicts
                resolve_conflicts.main()
                
                # After resolving, save state
                save_merge_state(repo_path, source_branch_name or "unknown", current)
                
                # Try to commit
                print("\n✅ Conflicts resolved! Committing merge...")
                if atomic_git_operation:
                    result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["commit", "--no-edit"],
                        description="merge commit"
                    )
                else:
                    result = run_git(["commit", "--no-edit"], cwd=repo_path, check=False)
                
                if result.returncode == 0:
                    print(f"✅ Merge committed successfully!")
                    clear_merge_cache(repo_path)
                else:
                    print(f"⚠️  Commit had issues: {result.stderr}")
                    save_merge_state(repo_path, source_branch_name or "unknown", current)
                return
                
            elif choice == '2':
                print("\n💾 Saving current merge state...")
                save_merge_state(repo_path, source_branch_name or "unknown", current)
                run_git(["merge", "--abort"], cwd=repo_path)
                print("✓ Merge aborted, state saved to cache")
                return
                
            elif choice == '3':
                confirm = input("\n⚠️  Abort and LOSE resolutions? (y/n): ").strip().lower()
                if confirm == 'y':
                    run_git(["merge", "--abort"], cwd=repo_path)
                    print("✓ Merge aborted")
                return
                
            else:
                result = run_git(["status"], cwd=repo_path)
                print(result.stdout)
                return
        else:
            # No conflicts, ready to commit
            print("\n✅ No conflicts detected. Ready to commit.")
            print("\nWhat would you like to do?")
            print("  1. Commit the merge now")
            print("  2. Review changes first")
            
            choice = input("\nChoice (1-2): ").strip()
            
            if choice == '1':
                # Commit with atomic operation
                if atomic_git_operation:
                    result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["commit", "--no-edit"],
                        description="merge commit"
                    )
                else:
                    result = run_git(["commit", "--no-edit"], cwd=repo_path, check=False)
                
                if result.returncode == 0:
                    print(f"✅ Merge committed successfully!")
                    print(result.stdout)
                    clear_merge_cache(repo_path)
                else:
                    print(f"❌ Commit failed: {result.stderr}")
                return
            else:
                result = run_git(["status"], cwd=repo_path)
                print(result.stdout)
                return
    
    # Check for cached merge state from previous attempt
    cached_state = load_merge_state(repo_path)
    if cached_state and cached_state.get('resolved_files'):
        print(f"\n💾 Found cached merge state:")
        print(f"   {cached_state.get('source')} → {cached_state.get('target')}")
        print(f"   {len(cached_state['resolved_files'])} files previously resolved")
        
        choice = input("\nRestore and retry this merge? (y/n): ").strip().lower()
        
        if choice == 'y':
            if restore_merge_state(repo_path):
                # Now retry the merge with restored files
                source = cached_state.get('source')
                target = cached_state.get('target')
                
                print(f"\n🔀 Retrying merge: {source} → {target}")
                
                # The files are already staged, just need to commit
                if atomic_git_operation:
                    result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=["commit", "-m", f"Merge {source} into {target}"],
                        description="merge commit"
                    )
                else:
                    result = run_git(["commit", "-m", f"Merge {source} into {target}"], cwd=repo_path, check=False)
                
                if result.returncode == 0:
                    print(f"✅ Merge completed successfully!")
                    clear_merge_cache(repo_path)
                else:
                    print(f"⚠️  Commit had issues, but state is saved")
                return
            else:
                print("Cancelled, starting fresh merge...")
        else:
            clear = input("Clear cached state? (y/n): ").strip().lower()
            if clear == 'y':
                clear_merge_cache(repo_path)
    
    # Show current branch
    current_branch = get_current_branch(repo_path)
    print(f"\n📍 Current branch: {current_branch}")
    
    # Get list of branches
    branches = get_branches(repo_path, include_remote=False)
    other_branches = [b for b in branches if b != current_branch]
    
    if not other_branches:
        print("\n❌ No other branches available to merge")
        return
    
    print(f"\n📋 Available branches to merge into '{current_branch}':")
    for i, branch in enumerate(other_branches, 1):
        print(f"  {i}. {branch}")
    
    # Ask which branch to merge FIRST (before any stashing)
    choice = input(f"\nEnter number or branch name (or 'c' to cancel): ").strip()
    
    if choice.lower() == 'c':
        print("Cancelled")
        return
    
    # Parse choice
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(other_branches):
            source_branch = other_branches[idx]
        else:
            print("Invalid choice")
            return
    else:
        source_branch = choice
        if source_branch not in branches:
            print(f"❌ Branch '{source_branch}' not found")
            return
    
    # Ask about merge strategy
    print(f"\n🔀 Merge strategy:")
    print("  1. Default (auto-merge)")
    print("  2. Ours (prefer current branch in conflicts)")
    print("  3. Theirs (prefer incoming branch in conflicts)")
    print("  4. No-commit (merge but don't auto-commit)")
    
    strategy_choice = input("\nChoice (1-4, default=1): ").strip() or '1'
    
    strategy = None
    no_commit = False
    
    if strategy_choice == '2':
        strategy = 'ours'
    elif strategy_choice == '3':
        strategy = 'theirs'
    elif strategy_choice == '4':
        no_commit = True
    
    # NOW check for uncommitted changes (but handle ignorable ones automatically)
    if has_uncommitted_changes(repo_path):
        # Check if it's ONLY ignorable changes
        if has_ignored_changes and has_ignored_changes(repo_path):
            ignorable_only = True
            # Check if there are non-ignorable changes too
            result = run_git(["status", "--porcelain"], cwd=repo_path)
            from gitship.gitops import get_ignore_patterns
            patterns = get_ignore_patterns(repo_path)
            
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                filepath = line.split(None, 1)[1] if len(line.split(None, 1)) == 2 else ""
                path = Path(filepath)
                matches_pattern = any(path.match(p) for p in patterns)
                if not matches_pattern:
                    ignorable_only = False
                    break
            
            if ignorable_only:
                print("\n📝 Detected ignorable changes (will be auto-stashed during merge)")
            else:
                print("\n⚠️  You have uncommitted changes (including non-ignorable files)")
                print("\nWhat would you like to do?")
                print("  1. Commit changes first")
                print("  2. Stash ALL changes and continue")
                print("  3. Cancel")
                
                ch = input("\nChoice (1-3): ").strip()
                
                if ch == '1':
                    print("\n💡 Please commit your changes first: gitship commit")
                    return
                elif ch == '2':
                    print("\n📦 Stashing all changes...")
                    result = run_git(["stash", "push", "-m", f"Auto-stash before merging {source_branch}"], cwd=repo_path)
                    if result.returncode == 0:
                        print("✓ Changes stashed")
                    else:
                        print(f"❌ Failed to stash: {result.stderr}")
                        return
                else:
                    print("Cancelled")
                    return
        else:
            # No gitops available, ask user
            print("\n⚠️  You have uncommitted changes.")
            print("\nWhat would you like to do?")
            print("  1. Commit changes first")
            print("  2. Stash changes and continue")
            print("  3. Cancel")
            
            ch = input("\nChoice (1-3): ").strip()
            
            if ch == '1':
                print("\n💡 Please commit your changes first: gitship commit")
                return
            elif ch == '2':
                print("\n📦 Stashing changes...")
                result = run_git(["stash", "push", "-m", f"Auto-stash before merging {source_branch}"], cwd=repo_path)
                if result.returncode == 0:
                    print("✓ Changes stashed")
                else:
                    print(f"❌ Failed to stash: {result.stderr}")
                    return
            else:
                print("Cancelled")
                return
    
    # Perform merge (atomic operation will handle ignorable changes)
    if no_commit:
        result = run_git(["merge", "--no-commit", source_branch], cwd=repo_path, check=False)
        if result.returncode == 0:
            print(f"✅ Merged '{source_branch}' into '{current_branch}' (not committed)")
            print("\n💡 Review changes and run 'git commit' when ready")
        else:
            if "CONFLICT" in result.stdout:
                print(f"\n⚠️  Merge has conflicts!")
                print("\nRun 'gitship merge' again to resolve interactively")
                save_merge_state(repo_path, source_branch, current_branch)
            else:
                print(f"❌ Merge failed: {result.stderr}")
    else:
        success = merge_branch(repo_path, source_branch, strategy)
        if not success:
            print("\n💡 To resolve: gitship merge")
            print("💡 To abort: git merge --abort")
            # Save state even on failure
            save_merge_state(repo_path, source_branch, current_branch)
        # Success case: merge_branch already cleared cache
    
    if choice.lower() == 'c':
        print("Cancelled")
        return
    
    # Parse choice
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(other_branches):
            source_branch = other_branches[idx]
        else:
            print("Invalid choice")
            return
    else:
        source_branch = choice
        if source_branch not in branches:
            print(f"❌ Branch '{source_branch}' not found")
            return
    
    # Ask about merge strategy
    print(f"\n🔀 Merge strategy:")
    print("  1. Default (auto-merge)")
    print("  2. Ours (prefer current branch in conflicts)")
    print("  3. Theirs (prefer incoming branch in conflicts)")
    print("  4. No-commit (merge but don't auto-commit)")
    
    strategy_choice = input("\nChoice (1-4, default=1): ").strip() or '1'
    
    strategy = None
    no_commit = False
    
    if strategy_choice == '2':
        strategy = 'ours'
    elif strategy_choice == '3':
        strategy = 'theirs'
    elif strategy_choice == '4':
        no_commit = True
    
    # Perform merge
    if no_commit:
        result = run_git(["merge", "--no-commit", source_branch], cwd=repo_path, check=False)
        if result.returncode == 0:
            print(f"✅ Merged '{source_branch}' into '{current_branch}' (not committed)")
            print("\n💡 Review changes and run 'git commit' when ready")
        else:
            if "CONFLICT" in result.stdout:
                print(f"\n⚠️  Merge has conflicts!")
                print("\nRun 'gitship resolve' to resolve them")
            else:
                print(f"❌ Merge failed: {result.stderr}")
    else:
        success = merge_branch(repo_path, source_branch, strategy)
        if not success:
            print("\n💡 To abort the merge: git merge --abort")

def main():
    """Entry point when called directly."""
    repo_path = Path.cwd()
    if not (repo_path / ".git").exists():
        print("❌ Not in a git repository")
        sys.exit(1)
    
    main_with_repo(repo_path)

if __name__ == "__main__":
    main()