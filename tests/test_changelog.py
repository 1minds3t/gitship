"""
Test suite for gitship changelog generation functionality.

This can be run with pytest or directly as a script.
"""

import sys
from pathlib import Path

# Add src to path for imports
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

try:
    from gitship.changelog_generator import (
        extract_file_changes_from_gitship_commit,
        get_detailed_commits_since_tag,
        GITSHIP_COMMIT_MARKER,
        run_git
    )
    IMPORTS_OK = True
except ImportError as e:
    print(f"WARNING: Could not import changelog_generator: {e}")
    IMPORTS_OK = False


class TestChangelogExtraction:
    """Test changelog extraction functionality."""
    
    SAMPLE_COMMIT = """Update 6 code files; Update configuration

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
    
    def test_imports(self):
        """Test that changelog_generator module can be imported."""
        assert IMPORTS_OK, "Failed to import changelog_generator module"
    
    def test_marker_constant(self):
        """Test that GITSHIP_COMMIT_MARKER is defined."""
        if not IMPORTS_OK:
            return  # Skip if imports failed
        
        assert GITSHIP_COMMIT_MARKER == "[gitship-generated]"
    
    def test_extract_file_changes(self):
        """Test extraction of file changes from gitship commit."""
        if not IMPORTS_OK:
            return
        
        result = extract_file_changes_from_gitship_commit(self.SAMPLE_COMMIT)
        
        # Should extract multiple lines
        assert len(result) > 0, "No lines extracted"
        
        # Should have section headers
        sections = [line for line in result if line.startswith("**")]
        assert "**New files:**" in sections
        assert "**Modified:**" in sections
        
        # Should have file entries
        file_lines = [line for line in result if line.startswith("- ")]
        assert len(file_lines) >= 7, f"Expected at least 7 file entries, got {len(file_lines)}"
    
    def test_marker_detection(self):
        """Test that gitship marker is detected in commit body."""
        if not IMPORTS_OK:
            return
        
        assert GITSHIP_COMMIT_MARKER in self.SAMPLE_COMMIT
    
    def test_extract_with_dash_bullets(self):
        """Test extraction works with dash bullets (-)."""
        if not IMPORTS_OK:
            return
        
        commit_with_dashes = """Update files

Modified:
  - file1.py (+10/-5 lines)
  - file2.py (+20/-10 lines)

[gitship-generated]
"""
        
        result = extract_file_changes_from_gitship_commit(commit_with_dashes)
        
        assert len(result) > 0
        assert "**Modified:**" in result
        file_lines = [line for line in result if line.startswith("- ")]
        assert len(file_lines) == 2
    
    def test_extract_empty_body(self):
        """Test extraction with empty body."""
        if not IMPORTS_OK:
            return
        
        result = extract_file_changes_from_gitship_commit("")
        assert len(result) == 0
    
    def test_extract_no_marker(self):
        """Test extraction still works without marker."""
        if not IMPORTS_OK:
            return
        
        commit_no_marker = """Update files

New files:
  • file1.py (100 lines)
"""
        
        result = extract_file_changes_from_gitship_commit(commit_no_marker)
        # Should still extract the content
        assert len(result) > 0


def run_tests_standalone():
    """Run tests without pytest."""
    print("Running changelog extraction tests...")
    print("=" * 70)
    
    if not IMPORTS_OK:
        print("❌ FAILED: Could not import changelog_generator module")
        return False
    
    test = TestChangelogExtraction()
    tests = [
        ("Import test", test.test_imports),
        ("Marker constant", test.test_marker_constant),
        ("Extract file changes", test.test_extract_file_changes),
        ("Marker detection", test.test_marker_detection),
        ("Extract with dash bullets", test.test_extract_with_dash_bullets),
        ("Extract empty body", test.test_extract_empty_body),
        ("Extract without marker", test.test_extract_no_marker),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            print(f"✅ PASSED: {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ FAILED: {name}")
            print(f"   {e}")
            failed += 1
        except Exception as e:
            print(f"❌ ERROR: {name}")
            print(f"   {e}")
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests_standalone()
    sys.exit(0 if success else 1)