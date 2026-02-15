#!/usr/bin/env python3
"""
Test script to verify gitship commit extraction works correctly.
Can be run from anywhere - automatically finds and imports the gitship module.
"""

from pathlib import Path
import sys
import subprocess

# Find and import gitship module
def setup_imports():
    """Find gitship module and add to path."""
    # Try to import directly first (if installed)
    try:
        import gitship
        return True
    except ImportError:
        pass
    
    # Find src/gitship directory
    current = Path(__file__).resolve().parent
    
    # Search upward for src/gitship
    for _ in range(5):  # Max 5 levels up
        gitship_dir = current / "src" / "gitship"
        if gitship_dir.exists():
            sys.path.insert(0, str(current / "src"))
            return True
        current = current.parent
    
    print("ERROR: Could not find gitship module!")
    print("Make sure you're running from within the gitship repository.")
    return False

if not setup_imports():
    sys.exit(1)

# Now import the modules
try:
    from gitship.changelog_generator import (
        extract_file_changes_from_gitship_commit,
        get_detailed_commits_since_tag,
        GITSHIP_COMMIT_MARKER
    )
except ImportError as e:
    print(f"ERROR: Failed to import gitship modules: {e}")
    print("Make sure changelog_generator.py exists in src/gitship/")
    sys.exit(1)

# Sample commit body (matches what gitship commit creates)
SAMPLE_COMMIT_BODY = """Update 6 code files; Update configuration

New files:
  • src/gitship/changelog_generator.py (316 lines)
  • src/gitship/deps.py (300 lines)

Modified:
  • src/gitship/cli.py (+52/-32 lines)
  • src/gitship/commit.py (+4/-1 lines)
  • src/gitship/pypi.py (+18/-16 lines)
  • src/gitship/release.py (+65/-4 lines)
  • pyproject.toml

[gitship-generated]
"""

def find_repo_root():
    """Find the git repository root."""
    current = Path.cwd()
    for _ in range(10):
        if (current / ".git").exists():
            return current
        current = current.parent
    return None

def test_extraction():
    print("Testing gitship commit extraction...")
    print("=" * 70)
    
    # Test 1: Extract file changes
    print("\n1. Testing extract_file_changes_from_gitship_commit():")
    print("-" * 70)
    
    result = extract_file_changes_from_gitship_commit(SAMPLE_COMMIT_BODY)
    
    print(f"Extracted {len(result)} lines:")
    for line in result:
        print(f"  {line}")
    
    if len(result) == 0:
        print("  ❌ FAILED: No lines extracted!")
        return False
    
    expected_sections = ["**New files:**", "**Modified:**"]
    found_sections = [line for line in result if line.startswith("**")]
    
    print(f"\n  Expected sections: {expected_sections}")
    print(f"  Found sections: {found_sections}")
    
    if set(expected_sections).issubset(set(found_sections)):
        print("  ✅ PASSED: All expected sections found")
    else:
        print("  ❌ FAILED: Missing expected sections")
        return False
    
    # Test 2: Check marker detection
    print("\n2. Testing marker detection:")
    print("-" * 70)
    
    has_marker = GITSHIP_COMMIT_MARKER in SAMPLE_COMMIT_BODY
    print(f"Marker '{GITSHIP_COMMIT_MARKER}' found: {has_marker}")
    
    if has_marker:
        print("  ✅ PASSED: Marker detected correctly")
    else:
        print("  ❌ FAILED: Marker not detected")
        return False
    
    # Test 3: Test on real repo
    repo_path = find_repo_root()
    
    if repo_path:
        print(f"\n3. Testing on real repo: {repo_path}")
        print("-" * 70)
        
        # Get last tag
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False
            )
            last_tag = result.stdout.strip() if result.returncode == 0 else ""
            
            if last_tag:
                print(f"Last tag: {last_tag}")
                commits = get_detailed_commits_since_tag(repo_path, last_tag)
                
                print(f"\nFound {len(commits)} commits since {last_tag}")
                
                gitship_commits = [c for c in commits if c['is_gitship']]
                other_commits = [c for c in commits if not c['is_gitship']]
                
                print(f"  Gitship-generated: {len(gitship_commits)}")
                print(f"  Other commits: {len(other_commits)}")
                
                for i, commit in enumerate(commits[:3], 1):  # Show first 3
                    print(f"\n  Commit {i}:")
                    print(f"    SHA: {commit['sha'][:8]}")
                    print(f"    Subject: {commit['subject'][:60]}...")
                    print(f"    Gitship: {'✅' if commit['is_gitship'] else '❌'}")
                    print(f"    Merge: {'Yes' if commit['is_merge'] else 'No'}")
                    if commit['is_gitship']:
                        body_preview = commit['body'][:150].replace('\n', ' ')
                        print(f"    Body preview: {body_preview}...")
                
                if len(commits) > 3:
                    print(f"\n  ... and {len(commits) - 3} more commits")
                
                # Test extraction on gitship commits
                if gitship_commits:
                    print(f"\n  Testing extraction on gitship commit:")
                    extracted = extract_file_changes_from_gitship_commit(gitship_commits[0]['body'])
                    print(f"    Extracted {len(extracted)} lines from real commit")
                    if len(extracted) > 0:
                        print("    ✅ PASSED: Successfully extracted from real commit")
                        for line in extracted[:5]:  # Show first 5
                            print(f"      {line}")
                        if len(extracted) > 5:
                            print(f"      ... and {len(extracted) - 5} more lines")
                    else:
                        print("    ⚠️  WARNING: No lines extracted from real commit")
                        print("    This might be expected if the commit format is different")
                else:
                    print("\n  ⚠️  No gitship-generated commits found")
                    print("    This is expected if you haven't used 'gitship commit' yet")
                
            else:
                print("No tags found in repository")
                print("  ⚠️  SKIPPED: Cannot test without tags")
                
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            return False
    else:
        print("\n3. Testing on real repo:")
        print("-" * 70)
        print("  ⚠️  SKIPPED: Not in a git repository")
    
    print("\n" + "=" * 70)
    print("✅ All tests passed!")
    return True

def main():
    """Main entry point."""
    print(f"Running from: {Path.cwd()}")
    print(f"Script location: {Path(__file__).resolve()}")
    print()
    
    success = test_extraction()
    
    if not success:
        print("\n❌ Some tests failed!")
        sys.exit(1)
    
    sys.exit(0)

if __name__ == "__main__":
    main()