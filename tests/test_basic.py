"""Basic tests for gitship package."""

import pytest
from pathlib import Path


def test_package_imports():
    """Test that the package can be imported."""
    import gitship
    assert gitship.__version__ == "0.1.0"
    assert gitship.__author__ == "1minds3t"


def test_checkgit_import():
    """Test that checkgit module can be imported."""
    from gitship import checkgit
    assert hasattr(checkgit, 'main')


def test_fixgit_import():
    """Test that fixgit module can be imported."""
    from gitship import fixgit
    assert hasattr(fixgit, 'main')
    assert hasattr(fixgit, 'main_with_args')


def test_git_command_helper():
    """Test git command helper function."""
    from gitship.checkgit import run_git_command
    
    # Test git version command
    result = run_git_command(["--version"])
    assert result.returncode == 0
    assert "git version" in result.stdout.lower()


def test_is_git_repo():
    """Test git repository detection."""
    from gitship.checkgit import is_git_repo
    import tempfile
    import subprocess
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Not a git repo initially
        assert not is_git_repo(tmppath)
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
        
        # Now it should be a git repo
        assert is_git_repo(tmppath)
