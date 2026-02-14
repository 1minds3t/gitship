"""Tests for reviewgit module."""

import pytest
from pathlib import Path
import tempfile
import subprocess
import sys


def test_reviewgit_imports():
    """Test that reviewgit module can be imported."""
    from gitship import reviewgit
    assert hasattr(reviewgit, 'main')
    assert hasattr(reviewgit, 'main_with_args')
    assert hasattr(reviewgit, 'get_last_tag')
    assert hasattr(reviewgit, 'get_commits_between')


def test_get_last_tag():
    """Test getting the last tag from a repository."""
    from gitship.reviewgit import get_last_tag
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
        
        # Create initial commit
        (tmppath / "test.txt").write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmppath, capture_output=True)
        
        # No tags yet
        assert get_last_tag(tmppath) is None
        
        # Create a tag
        subprocess.run(["git", "tag", "v1.0.0"], cwd=tmppath, capture_output=True)
        
        # Should find the tag
        assert get_last_tag(tmppath) == "v1.0.0"


def test_get_commits_between():
    """Test getting commits between two references."""
    from gitship.reviewgit import get_commits_between
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
        
        # Create commits
        for i in range(3):
            (tmppath / f"file{i}.txt").write_text(f"content {i}")
            subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Commit {i}"], cwd=tmppath, capture_output=True)
            if i == 0:
                subprocess.run(["git", "tag", "v1.0.0"], cwd=tmppath, capture_output=True)
        
        # Get commits between tag and HEAD
        commits = get_commits_between(tmppath, "v1.0.0", "HEAD")
        
        # Should have 2 commits (1 and 2, not including the tagged commit 0)
        assert len(commits) == 2
        assert commits[0]['subject'] == "Commit 2"
        assert commits[1]['subject'] == "Commit 1"


def test_create_export_filename():
    """Test export filename generation."""
    from gitship.reviewgit import create_export_filename
    
    filename = create_export_filename("myrepo", "v1.0.0", "HEAD")
    
    assert "myrepo" in filename
    assert "v1.0.0" in filename
    assert "HEAD" in filename
    assert filename.endswith(".txt")
    
    # Test with refs containing slashes
    filename2 = create_export_filename("myrepo", "feature/branch", "main")
    assert "/" not in filename2  # Slashes should be replaced


def test_diff_stat():
    """Test getting diff statistics."""
    from gitship.reviewgit import get_diff_stat
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
        
        # Create initial commit
        (tmppath / "file1.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "tag", "v1.0.0"], cwd=tmppath, capture_output=True)
        
        # Make changes
        (tmppath / "file1.txt").write_text("modified")
        (tmppath / "file2.txt").write_text("new file")
        subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Changes"], cwd=tmppath, capture_output=True)
        
        # Get diff stat
        stat = get_diff_stat(tmppath, "v1.0.0", "HEAD")
        
        assert "file1.txt" in stat
        assert "file2.txt" in stat


if __name__ == "__main__":
    pytest.main([__file__, "-v"])