#!/usr/bin/env python3
"""
amend - Amend the last commit with smart message generation.

Allows rewriting the last commit message using the same analysis
as the commit command.
"""

import subprocess
import sys
from pathlib import Path

try:
    from gitship.merge_message import generate_merge_message
except ImportError:
    generate_merge_message = None

try:
    from gitship.gitops import atomic_git_operation, has_ignored_changes
except ImportError:
    atomic_git_operation = None
    has_ignored_changes = None


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
        raise


def get_last_commit_info(repo_path: Path):
    """Get info about the last commit."""
    result = run_git([
        "log", "-1",
        "--pretty=format:%H|%h|%s|%b"
    ], cwd=repo_path, check=False)
    
    if result.returncode != 0:
        return None
    
    parts = result.stdout.split('|', 3)
    if len(parts) < 4:
        return None
    
    return {
        'hash': parts[0],
        'short': parts[1],
        'subject': parts[2],
        'body': parts[3]
    }


def is_merge_commit(repo_path: Path) -> bool:
    """Check if last commit is a merge commit."""
    result = run_git(["log", "-1", "--pretty=format:%P"], cwd=repo_path, check=False)
    parents = result.stdout.strip().split()
    return len(parents) > 1


def get_merged_branches(repo_path: Path):
    """Get the branches that were merged in last commit."""
    result = run_git([
        "log", "-1",
        "--pretty=format:%s"
    ], cwd=repo_path, check=False)
    
    subject = result.stdout.strip()
    
    # Try to parse "Merge branch 'X' into Y"
    if "Merge branch" in subject:
        parts = subject.split("'")
        if len(parts) >= 2:
            source = parts[1]
            if "into" in subject:
                target_parts = subject.split("into")
                if len(target_parts) > 1:
                    target = target_parts[1].strip().strip("'")
                    return source, target
    
    return None, None


def amend_with_smart_message(repo_path: Path):
    """Amend last commit with smart message generation."""
    
    # Get last commit info
    commit = get_last_commit_info(repo_path)
    if not commit:
        print("❌ Could not get last commit info")
        return False
    
    print(f"\n📝 Last commit: {commit['short']} - {commit['subject']}")
    
    # Check if it's a merge
    if is_merge_commit(repo_path):
        print(f"\n🔀 Detected merge commit")
        
        if not generate_merge_message:
            print("⚠️  merge_message module not available")
            print("Opening editor for manual edit...")
            subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
            return True
        
        # Get the parent commits
        result = run_git(["log", "-1", "--pretty=format:%P"], cwd=repo_path, check=False)
        parents = result.stdout.strip().split()
        
        if len(parents) < 2:
            print("⚠️  Could not determine merge parents")
            subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
            return True
        
        print(f"📊 Analyzing merge between {parents[1][:7]} and {parents[0][:7]}...")
        
        # Try to detect branch names for better message
        source, target = get_merged_branches(repo_path)
        
        # If can't detect from commit message, use current branch
        if not target:
            target = run_git(["branch", "--show-current"], cwd=repo_path, check=False).stdout.strip()
        
        # Generate detailed message using parent commits
        try:
            # Use parent[1] as base (target before merge) and parent[0] as head (source merged in)
            detailed_message = generate_merge_message(repo_path, parents[1], parents[0])
            
            # If we detected branch names, update the title
            if source and target:
                lines = detailed_message.split('\n')
                lines[0] = f"Merge {source} → {target}"
                detailed_message = '\n'.join(lines)
            
            print("\n" + "="*70)
            print("PROPOSED DETAILED MESSAGE:")
            print("="*70)
            print(detailed_message)
            print("="*70)
            
            choice = input("\nUse this message? (y/n/e to edit in editor): ").strip().lower()
            
            if choice == 'y':
                result = run_git(["commit", "--amend", "-m", detailed_message], cwd=repo_path, check=False)
                if result.returncode == 0:
                    print("✅ Commit message updated!")
                    
                    # Offer to push
                    should_push = input("\n🚀 Push to remote? (y/n): ").strip().lower()
                    if should_push == 'y':
                        push_amended_commit(repo_path)
                    
                    return True
                else:
                    print(f"❌ Amend failed: {result.stderr}")
                    return False
                    
            elif choice == 'e':
                # Write message to temp file for editor
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    f.write(detailed_message)
                    temp_path = f.name
                
                # Open in git commit amend with temp file
                result = subprocess.run(
                    ["git", "commit", "--amend", "-F", temp_path, "--edit"],
                    cwd=repo_path
                )
                
                import os
                os.unlink(temp_path)
                
                if result.returncode == 0:
                    print("✅ Commit message updated via editor!")
                    
                    # Offer to push
                    should_push = input("\n🚀 Push to remote? (y/n): ").strip().lower()
                    if should_push == 'y':
                        push_amended_commit(repo_path)
                    
                    return True
                else:
                    print("❌ Editor cancelled or failed")
                    return False
            else:
                print("Cancelled")
                return False
                
        except Exception as e:
            print(f"⚠️  Error generating message: {e}")
            print("Opening editor for manual edit...")
            subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
            return True
    else:
        print("\n💡 Regular commit - opening editor to amend")
        print("    (For smart commit messages, use 'gitship commit' next time)")
        subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
        return True


def amend_older_commit(repo_path: Path):
    """Amend an older commit using interactive rebase with smart message generation."""
    
    # Check if there's already a rebase in progress
    rebase_merge = repo_path / ".git" / "rebase-merge"
    rebase_apply = repo_path / ".git" / "rebase-apply"
    
    if rebase_merge.exists() or rebase_apply.exists():
        print("\n⚠️  There's already a rebase in progress!")
        print("\nWhat would you like to do?")
        print("  1. Abort the existing rebase and start fresh")
        print("  2. Continue the existing rebase")
        print("  3. Cancel")
        
        choice = input("\nChoice (1-3): ").strip()
        
        if choice == '1':
            print("\n🔄 Aborting existing rebase...")
            abort_result = run_git(["rebase", "--abort"], cwd=repo_path, check=False)
            if abort_result.returncode != 0:
                print(f"❌ Failed to abort: {abort_result.stderr}")
                return False
            print("✓ Aborted")
        elif choice == '2':
            print("\n💡 Please complete the existing rebase manually:")
            print("   - Edit files if needed")
            print("   - git add <files>")
            print("   - git rebase --continue")
            return False
        else:
            print("Cancelled")
            return False
    
    # First, handle any unstaged changes
    if has_ignored_changes and has_ignored_changes(repo_path):
        print("\n⚠️  You have unstaged changes that will be auto-stashed during rebase")
    
    print("\n📜 Recent commits:")
    print("="*60)
    
    # Get commits WITHOUT graph for clean numbered display
    result = run_git([
        "log", "-15", "--oneline", "--decorate"
    ], cwd=repo_path, check=False)
    
    if result.returncode != 0:
        print("❌ Could not get commit history")
        return False
    
    commits = []
    commit_info = {}
    
    for idx, line in enumerate(result.stdout.strip().split('\n'), 1):
        # Parse: hash + message
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        
        commit_hash = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        
        # Check if it's a merge
        merge_check = run_git(["log", "-1", "--pretty=format:%P", commit_hash], cwd=repo_path, check=False)
        is_merge = len(merge_check.stdout.strip().split()) > 1
        
        commits.append(commit_hash)
        commit_info[commit_hash] = {'is_merge': is_merge}
        
        # Display with number
        merge_indicator = " 🔀" if is_merge else ""
        print(f"  {idx:2}. {commit_hash} {message}{merge_indicator}")
    
    print("\n💡 Enter commit number (1-15) or hash to amend")
    print("   Or 'c' to cancel")
    
    choice = input("\nChoice: ").strip()
    
    if choice.lower() == 'c':
        print("Cancelled")
        return False
    
    # Parse choice
    target_hash = None
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(commits):
            target_hash = commits[idx]
        else:
            print("❌ Invalid commit number")
            return False
    else:
        # Assume it's a commit hash
        target_hash = choice
        if target_hash not in commits:
            # Try to find it in git
            verify = run_git(["rev-parse", "--verify", target_hash], cwd=repo_path, check=False)
            if verify.returncode != 0:
                print(f"❌ Invalid commit hash: {target_hash}")
                return False
    
    print(f"\n🔧 Amending commit: {target_hash}")
    
    # Check if this is HEAD
    head_hash = run_git(["rev-parse", "HEAD"], cwd=repo_path, check=False).stdout.strip()
    is_head = (target_hash == head_hash or target_hash == head_hash[:7] or head_hash.startswith(target_hash))
    
    # Get commit info
    is_merge = commit_info.get(target_hash, {}).get('is_merge', False)
    
    # Generate smart message if it's a merge
    new_message = None
    if is_merge and generate_merge_message:
        print("🔀 This is a merge commit - generating smart message...")
        
        # Get parent commits
        parents_result = run_git(["log", "-1", "--pretty=format:%P", target_hash], cwd=repo_path, check=False)
        parents = parents_result.stdout.strip().split()
        
        if len(parents) >= 2:
            try:
                # Generate detailed merge message
                detailed_message = generate_merge_message(repo_path, parents[1], parents[0])
                
                print("\n" + "="*70)
                print("PROPOSED MESSAGE FOR THIS MERGE:")
                print("="*70)
                print(detailed_message)
                print("="*70)
                
                use_smart = input("\nUse this smart message? (y/n, default=y): ").strip().lower()
                if use_smart == 'n':
                    print("Cancelled")
                    return False
                
                new_message = detailed_message
                        
            except Exception as e:
                print(f"⚠️  Error: {e}")
                return False
        else:
            print("⚠️  Could not determine merge parents")
            return False
    
    # If no smart message, ask for manual input or editor
    if not new_message:
        print("\n📝 Opening editor to write new commit message...")
        import tempfile
        
        # Get current message
        current_msg = run_git(["log", "-1", "--pretty=format:%B", target_hash], cwd=repo_path, check=False).stdout
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(current_msg)
            msg_file = f.name
        
        # Open editor
        editor = subprocess.os.environ.get('EDITOR', 'vi')
        result = subprocess.run([editor, msg_file])
        
        if result.returncode != 0:
            print("Editor cancelled")
            subprocess.os.unlink(msg_file)
            return False
        
        # Read new message
        with open(msg_file, 'r') as f:
            new_message = f.read()
        
        subprocess.os.unlink(msg_file)
        
        if not new_message.strip():
            print("❌ Empty commit message, cancelled")
            return False
    
    # Now rewrite the commit with the new message
    if is_head:
        # Simple case - just amend HEAD
        print("\n💾 Amending HEAD commit...")
        result = run_git(["commit", "--amend", "-m", new_message], cwd=repo_path, check=False)
        if result.returncode == 0:
            print("✅ Commit message updated!")
            
            # Offer to push
            should_push = input("\n🚀 Push to remote? (y/n): ").strip().lower()
            if should_push == 'y':
                push_amended_commit(repo_path)
            
            return True
        else:
            print(f"❌ Amend failed: {result.stderr}")
            return False
    else:
        # Complex case - rewrite history
        print("\n⚠️  This will rewrite git history (all commits after this one will change)")
        
        confirm = input("\nContinue with history rewrite? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Cancelled")
            return False
        
        print("\n🔧 Rewriting commit history...")
        
        # Use git filter-branch to rewrite just this commit's message
        import tempfile
        
        # Create a script that rewrites the message
        script = f"""#!/bin/bash
if [ "$GIT_COMMIT" = "{target_hash}" ]; then
    cat <<'EOFMSG'
{new_message}
EOFMSG
else
    cat
fi
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script)
            script_file = f.name
        
        subprocess.os.chmod(script_file, 0o755)
        
        try:
            # Stash any changes first
            stashed = False
            if has_ignored_changes and has_ignored_changes(repo_path):
                print("🔒 Stashing changes...")
                from gitship.gitops import stash_ignored_changes
                stashed = stash_ignored_changes(repo_path, "before filter-branch")
            
            # Run filter-branch
            result = run_git([
                "filter-branch",
                "-f",
                "--msg-filter",
                script_file,
                "--",
                f"{target_hash}..HEAD"
            ], cwd=repo_path, check=False)
            
            # Restore stash
            if stashed:
                print("↩️  Restoring stashed changes...")
                from gitship.gitops import restore_latest_stash
                restore_latest_stash(repo_path)
            
            if result.returncode == 0:
                print("✅ Commit history rewritten successfully!")
                print(f"💡 Commit {target_hash[:7]} message has been updated")
                print("💡 All subsequent commits have new hashes")
                
                # Offer to push
                should_push = input("\n🚀 Force push to remote? (y/n): ").strip().lower()
                if should_push == 'y':
                    push_amended_commit(repo_path, force=True)
                
                return True
            else:
                print(f"❌ Filter-branch failed: {result.stderr}")
                return False
                
        finally:
            subprocess.os.unlink(script_file)


def push_amended_commit(repo_path: Path, force: bool = False):
    """Push amended commit to remote - works for any branch."""
    
    # Get current branch - use branch.py if available, else fallback
    try:
        from gitship.branch import get_current_branch as branch_get_current
        branch = branch_get_current(repo_path)
    except:
        branch_result = run_git(["branch", "--show-current"], cwd=repo_path, check=False)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    
    if not branch:
        print("❌ Could not determine current branch")
        return False
    
    # Determine remote - try origin first, then any remote
    remote_result = run_git(["remote"], cwd=repo_path, check=False)
    if remote_result.returncode != 0 or not remote_result.stdout.strip():
        print("❌ No git remotes configured")
        return False
    
    remotes = remote_result.stdout.strip().split('\n')
    remote = 'origin' if 'origin' in remotes else remotes[0]
    
    # Build push command
    if force:
        print(f"\n⚠️  Force pushing to {remote}/{branch} (rewrites history)")
        confirm = input("Continue? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Cancelled")
            return False
        push_cmd = ["push", "--force-with-lease", remote, branch]
    else:
        # Regular push - set upstream if it doesn't exist
        push_cmd = ["push", "-u", remote, branch]
    
    print(f"\n📤 Pushing to {remote}/{branch}...")
    
    # Use atomic operation if available to handle any stashed changes
    if atomic_git_operation:
        result = atomic_git_operation(
            repo_path=repo_path,
            git_command=push_cmd,
            description=f"push to {remote}/{branch}"
        )
    else:
        result = run_git(push_cmd, cwd=repo_path, check=False)
    
    if result.returncode == 0:
        print("✅ Pushed successfully!")
        return True
    else:
        print(f"❌ Push failed: {result.stderr}")
        
        # Check for common issues and provide helpful hints
        if "rejected" in result.stderr.lower() and "non-fast-forward" in result.stderr.lower():
            print("\n💡 Remote has changes you don't have.")
            print("   Attempting automatic pull --rebase and retry...")
            
            # Try to pull --rebase automatically
            if atomic_git_operation:
                pull_result = atomic_git_operation(
                    repo_path=repo_path,
                    git_command=["pull", "--rebase", remote, branch],
                    description=f"pull --rebase {remote}/{branch}"
                )
            else:
                pull_result = run_git(["pull", "--rebase", remote, branch], cwd=repo_path, check=False)
            
            if pull_result.returncode == 0:
                print("✅ Pull --rebase successful, retrying push...")
                
                # Retry push
                if atomic_git_operation:
                    retry_result = atomic_git_operation(
                        repo_path=repo_path,
                        git_command=push_cmd,
                        description=f"push to {remote}/{branch} after rebase"
                    )
                else:
                    retry_result = run_git(push_cmd, cwd=repo_path, check=False)
                
                if retry_result.returncode == 0:
                    print("✅ Pushed successfully after rebase!")
                    return True
                else:
                    print(f"❌ Push still failed after rebase: {retry_result.stderr}")
                    return False
            else:
                print(f"❌ Pull --rebase failed: {pull_result.stderr}")
                print("   Please resolve conflicts manually, then run: git push")
                return False
        elif "no upstream" in result.stderr.lower():
            print(f"\n💡 No upstream branch. Try: git push -u {remote} {branch}")
        
        return False


def main_with_repo(repo_path: Path):
    """Interactive amend workflow."""
    
    print("\n🔧 AMEND LAST COMMIT")
    print("="*60)
    
    # Check if there's a commit to amend
    result = run_git(["log", "-1", "--oneline"], cwd=repo_path, check=False)
    if result.returncode != 0:
        print("❌ No commits to amend")
        return
    
    print(f"Last commit: {result.stdout.strip()}")
    
    print("\nOptions:")
    print("  1. Smart amend (generate detailed message for merges)")
    print("  2. Manual amend (open editor)")
    print("  3. Amend older commit (interactive rebase)")
    print("  4. Cancel")
    
    choice = input("\nChoice (1-4): ").strip()
    
    if choice == '1':
        amend_with_smart_message(repo_path)
    elif choice == '2':
        print("\n📝 Opening editor...")
        result = subprocess.run(["git", "commit", "--amend"], cwd=repo_path)
        if result.returncode == 0:
            print("✅ Done")
            
            # Offer to push
            should_push = input("\n🚀 Push to remote? (y/n): ").strip().lower()
            if should_push == 'y':
                push_amended_commit(repo_path)
    elif choice == '3':
        amend_older_commit(repo_path)
    else:
        print("Cancelled")


def main():
    """Entry point when called directly."""
    repo_path = Path.cwd()
    if not (repo_path / ".git").exists():
        print("❌ Not in a git repository")
        sys.exit(1)
    
    main_with_repo(repo_path)


if __name__ == "__main__":
    main()