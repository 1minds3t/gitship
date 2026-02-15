#!/usr/bin/env python3
"""
pypi - PyPI publishing automation for gitship.

Handles:
- GitHub Actions workflow generation for OIDC publishing
- PyPI package status detection
- Trusted publisher setup guidance
- Release coordination with GitHub
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, Tuple
from glob import glob

# Use tomllib (Python 3.11+) or fallback to tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# Optional: requests for PyPI check
try:
    import requests
except ImportError:
    requests = None


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_CYAN = '\033[96m'


def run_command(args: list, cwd: Path = None, capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=False
    )


def read_package_name(repo_path: Path) -> Optional[str]:
    """Read package name from pyproject.toml using proper TOML parser."""
    pyproject_path = repo_path / "pyproject.toml"
    
    if not pyproject_path.exists():
        return None
    
    if tomllib is None:
        print(f"{Colors.YELLOW}Warning: tomllib not available. Install tomli: pip install tomli{Colors.RESET}")
        # Fallback to manual parsing (fragile but works)
        try:
            with open(pyproject_path, 'r') as f:
                for line in f:
                    if line.strip().startswith('name = '):
                        name = line.split('=')[1].strip().strip('"').strip("'")
                        return name
        except Exception as e:
            print(f"{Colors.YELLOW}Warning: Could not read pyproject.toml: {e}{Colors.RESET}")
        return None
    
    try:
        with open(pyproject_path, 'rb') as f:
            data = tomllib.load(f)
        
        return data.get('project', {}).get('name')
    
    except Exception as e:
        print(f"{Colors.YELLOW}Warning: Could not parse pyproject.toml: {e}{Colors.RESET}")
        return None


def get_github_repo_info(repo_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Get GitHub owner and repo name from git remote.
    
    Returns (owner, repo_name) or (None, None) if not found.
    """
    result = run_command(['git', 'remote', 'get-url', 'origin'], cwd=repo_path)
    
    if result.returncode != 0:
        return None, None
    
    remote_url = result.stdout.strip()
    
    # Parse different URL formats:
    # - https://github.com/owner/repo.git
    # - git@github.com:owner/repo.git
    # - git@github-custom:owner/repo.git (SSH config aliases)
    
    # Remove .git suffix
    if remote_url.endswith('.git'):
        remote_url = remote_url[:-4]
    
    # Extract owner/repo
    if 'github.com' in remote_url or 'github-' in remote_url:
        # HTTPS format
        if remote_url.startswith('https://'):
            parts = remote_url.split('/')
            if len(parts) >= 2:
                return parts[-2], parts[-1]
        
        # SSH format (git@github.com:owner/repo or git@github-alias:owner/repo)
        elif ':' in remote_url:
            after_colon = remote_url.split(':', 1)[1]
            parts = after_colon.split('/')
            if len(parts) >= 2:
                return parts[0], parts[1]
    
    return None, None


def check_pypi_status(package_name: str) -> str:
    """
    Check if package exists on PyPI.
    
    Returns:
        'missing' - Package not on PyPI
        'exists' - Package exists on PyPI
        'unknown' - Could not determine (network error, etc.)
    """
    if requests is None:
        print(f"{Colors.YELLOW}⚠ requests library not available{Colors.RESET}")
        print(f"  Install with: {Colors.DIM}python -m pip install requests{Colors.RESET}")
        return 'unknown'
    
    try:
        response = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=5)
        
        if response.status_code == 200:
            return 'exists'
        elif response.status_code == 404:
            return 'missing'
        else:
            return 'unknown'
    
    except Exception:
        return 'unknown'


def generate_publish_workflow(repo_path: Path, package_name: str, method: str = "oidc") -> str:
    """
    Generate GitHub Actions workflow for PyPI publishing.
    
    Args:
        repo_path: Path to repository
        package_name: Name of the package
        method: 'oidc' for trusted publisher, 'token' for API token
    
    Returns the workflow content as a string.
    """
    
    if method == "oidc":
        workflow = f"""name: Publish to PyPI

on:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  pypi-publish:
    name: Upload release to PyPI
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/{package_name}
    permissions:
      id-token: write  # IMPORTANT: this permission is mandatory for trusted publishing
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install build
      
      - name: Build package
        run: python -m build
      
      - name: Publish package distributions to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
"""
    else:  # token method
        workflow = f"""name: Publish to PyPI

on:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  pypi-publish:
    name: Upload release to PyPI
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install build
      
      - name: Build package
        run: python -m build
      
      - name: Publish package distributions to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{{{ secrets.PYPI_API_TOKEN }}}}
"""
    
    return workflow


def ensure_publish_workflow(repo_path: Path, package_name: str) -> tuple[bool, str]:
    """
    Create .github/workflows/publish.yml if missing.
    
    Returns (workflow_exists, method) where method is 'oidc' or 'token'.
    """
    workflows_dir = repo_path / ".github" / "workflows"
    publish_yml = workflows_dir / "publish.yml"
    
    if publish_yml.exists():
        print(f"{Colors.GREEN}✓ GitHub Actions workflow already exists{Colors.RESET}")
        # Detect which method is being used
        content = publish_yml.read_text()
        if "id-token: write" in content:
            return True, "oidc"
        else:
            return True, "token"
    
    print(f"\n{Colors.CYAN}📦 PyPI Publishing Setup{Colors.RESET}")
    print(f"{Colors.YELLOW}⚠ No GitHub Actions workflow found for PyPI publishing{Colors.RESET}\n")
    
    print("Choose publishing method:")
    print("  1. OIDC Trusted Publisher (recommended - no tokens needed)")
    print("  2. API Token (classic - works immediately if you have a token)")
    
    choice = input("\nChoice (1-2): ").strip()
    
    method = "oidc" if choice == "1" else "token"
    
    print("\nI can create a workflow that:")
    if method == "oidc":
        print("  • Uses OpenID Connect (OIDC) for secure publishing")
        print("  • Triggers on GitHub releases")
        print("  • Automatically builds and uploads to PyPI")
        print("  • Requires no API tokens (uses trusted publisher)")
    else:
        print("  • Uses PyPI API token for authentication")
        print("  • Triggers on GitHub releases")
        print("  • Automatically builds and uploads to PyPI")
    
    create = input(f"\n{Colors.BRIGHT_BLUE}Create .github/workflows/publish.yml? (y/n):{Colors.RESET} ").strip().lower()
    
    if create != 'y':
        print(f"{Colors.YELLOW}Skipped workflow creation{Colors.RESET}")
        return False, method
    
    # Create directories
    workflows_dir.mkdir(parents=True, exist_ok=True)
    
    # Write workflow
    workflow_content = generate_publish_workflow(repo_path, package_name, method)
    
    with open(publish_yml, 'w') as f:
        f.write(workflow_content)
    
    print(f"{Colors.GREEN}✓ Created .github/workflows/publish.yml{Colors.RESET}")
    
    # Stage the file
    result = run_command(['git', 'add', '.github/workflows/publish.yml'], cwd=repo_path)
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Staged workflow file for commit{Colors.RESET}")
    
    if method == "token":
        print(f"\n{Colors.YELLOW}⚠️  Don't forget to add your PyPI token as a GitHub secret:{Colors.RESET}")
        print(f"   1. Get token from: {Colors.BRIGHT_CYAN}https://pypi.org/manage/account/token/{Colors.RESET}")
        owner, repo = get_github_repo_info(repo_path)
        if owner and repo:
            print(f"   2. Go to: {Colors.BRIGHT_CYAN}https://github.com/{owner}/{repo}/settings/secrets/actions{Colors.RESET}")
        print(f"   3. Create secret: {Colors.GREEN}PYPI_API_TOKEN{Colors.RESET}")
    
    return True, method
    
    return True


def create_github_environment(repo_path: Path, owner: str, repo_name: str, env_name: str = "pypi") -> bool:
    """
    Create a GitHub environment using gh CLI API.
    
    Returns True if successful.
    """
    print(f"\n{Colors.CYAN}Creating GitHub environment '{env_name}'...{Colors.RESET}")
    
    # Check if gh CLI is available
    result = run_command(['gh', '--version'])
    if result.returncode != 0:
        print(f"{Colors.YELLOW}⚠ GitHub CLI not found - skipping auto-creation{Colors.RESET}")
        print(f"{Colors.DIM}   Manual setup: https://github.com/{owner}/{repo_name}/settings/environments/new{Colors.RESET}")
        return False
    
    # Create environment - use exact command that worked
    result = run_command([
        'gh', 'api',
        '--method', 'PUT',
        '-H', 'Accept: application/vnd.github+json',
        '-H', 'X-GitHub-Api-Version: 2022-11-28',
        f'/repos/{owner}/{repo_name}/environments/{env_name}'
    ], cwd=repo_path)
    
    print(f"[DEBUG] gh api exit code: {result.returncode}")
    print(f"[DEBUG] stdout: {result.stdout[:200] if result.stdout else 'EMPTY'}")
    print(f"[DEBUG] stderr: {result.stderr[:200] if result.stderr else 'EMPTY'}")
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Created GitHub environment '{env_name}'{Colors.RESET}")
        return True
    else:
        print(f"{Colors.YELLOW}⚠ Could not auto-create environment{Colors.RESET}")
        print(f"{Colors.DIM}   Manual setup: https://github.com/{owner}/{repo_name}/settings/environments/new{Colors.RESET}")
        return False


def guide_trusted_publisher_setup(repo_path: Path, package_name: str, username: str):
    """
    Interactive guide for setting up PyPI trusted publisher.
    """
    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}🔐 PYPI TRUSTED PUBLISHER SETUP{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.RESET}\n")
    
    print(f"{Colors.CYAN}This is a FIRST-TIME RELEASE for '{package_name}'{Colors.RESET}\n")
    
    # Auto-create GitHub environment
    owner, repo = get_github_repo_info(repo_path)
    if owner and repo:
        create_github_environment(repo_path, owner, repo, "pypi")
        print(f"{Colors.GREEN}✓ Step 1/2 complete: GitHub environment created{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ Could not detect GitHub repo info{Colors.RESET}")
    
    print(f"\n{Colors.BRIGHT_YELLOW}Final Step - Add Pending Publisher on PyPI:{Colors.RESET}")
    print(f"   Visit: {Colors.BRIGHT_CYAN}https://pypi.org/manage/account/publishing/{Colors.RESET}")
    print(f"   \n   Fill in:")
    print(f"   • PyPI Project Name: {Colors.GREEN}{package_name}{Colors.RESET}")
    print(f"   • Owner: {Colors.GREEN}{owner or username}{Colors.RESET}")
    print(f"   • Repository name: {Colors.GREEN}{repo or package_name}{Colors.RESET}")
    print(f"   • Workflow name: {Colors.GREEN}publish.yml{Colors.RESET}")
    print(f"   • Environment name: {Colors.GREEN}pypi{Colors.RESET}")
    
    print(f"\n{Colors.BRIGHT_YELLOW}How it works:{Colors.RESET}")
    print("   • You create a GitHub Release")
    print("   • Workflow runs automatically")
    print("   • PyPI verifies the workflow via OIDC")
    print("   • Package is published (no API tokens needed!)")
    
    print(f"\n{Colors.DIM}Note: The first release will CREATE the PyPI project automatically{Colors.RESET}")
    print(f"{Colors.DIM}      After that, it becomes a regular trusted publisher{Colors.RESET}")
    
    while True:
        ready = input(f"\n{Colors.BRIGHT_BLUE}Ready to continue? (y/n):{Colors.RESET} ").strip().lower()
        if ready == 'y':
            break
        elif ready == 'n':
            print(f"{Colors.YELLOW}Setup incomplete - please configure PyPI before publishing{Colors.RESET}")
            return
        else:
            print(f"{Colors.YELLOW}Please enter 'y' or 'n'{Colors.RESET}")


def create_github_release_draft(repo_path: Path, tag: str, changelog: str, package_name: str) -> bool:
    """
    Create a GitHub release draft using gh CLI.
    
    Returns True if successful.
    """
    print(f"\n{Colors.CYAN}Creating GitHub release draft...{Colors.RESET}")
    
    # Check if gh CLI is available
    result = run_command(['gh', '--version'])
    if result.returncode != 0:
        print(f"{Colors.YELLOW}⚠ GitHub CLI not found{Colors.RESET}")
        print(f"  Install with: {Colors.DIM}sudo apt install gh{Colors.RESET} or {Colors.DIM}brew install gh{Colors.RESET}")
        return False
    
    # Get owner and repo from git remote
    owner, repo = get_github_repo_info(repo_path)
    
    # Create release draft
    result = run_command([
        'gh', 'release', 'create',
        tag,
        '--draft',
        '--title', f'{package_name} {tag}',
        '--notes', changelog
    ], cwd=repo_path, capture_output=False)
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Created GitHub release draft{Colors.RESET}")
        
        if owner and repo:
            print(f"  View at: {Colors.BRIGHT_CYAN}https://github.com/{owner}/{repo}/releases{Colors.RESET}")
        else:
            print(f"  View releases in your repository settings")
        
        return True
    else:
        print(f"{Colors.RED}✗ Failed to create release draft{Colors.RESET}")
        return False


def offer_manual_publish(repo_path: Path):
    """
    Offer manual PyPI publishing using build + twine.
    """
    print(f"\n{Colors.CYAN}Manual PyPI Publishing:{Colors.RESET}\n")
    print("If you prefer to publish manually:")
    print(f"\n{Colors.DIM}# Install dependencies{Colors.RESET}")
    print(f"  python -m pip install build twine")
    print(f"\n{Colors.DIM}# Build package{Colors.RESET}")
    print(f"  python -m build")
    print(f"\n{Colors.DIM}# Upload to PyPI{Colors.RESET}")
    print(f"  python -m twine upload dist/*")
    
    manual = input(f"\n{Colors.BRIGHT_BLUE}Build and upload now? (y/n):{Colors.RESET} ").strip().lower()
    
    if manual == 'y':
        print(f"\n{Colors.CYAN}Building package...{Colors.RESET}")
        
        # Install build and twine using current interpreter
        result = run_command(
            [sys.executable, '-m', 'pip', 'install', 'build', 'twine'],
            cwd=repo_path,
            capture_output=False
        )
        if result.returncode != 0:
            print(f"{Colors.RED}✗ Failed to install dependencies{Colors.RESET}")
            return
        
        # Build
        result = run_command(
            [sys.executable, '-m', 'build'],
            cwd=repo_path,
            capture_output=False
        )
        if result.returncode != 0:
            print(f"{Colors.RED}✗ Build failed{Colors.RESET}")
            return
        
        print(f"{Colors.GREEN}✓ Package built{Colors.RESET}")
        
        # Upload
        upload = input(f"\n{Colors.YELLOW}Upload to PyPI? (y/n):{Colors.RESET} ").strip().lower()
        if upload == 'y':
            # Use glob to expand dist/* properly
            dist_files = glob(str(repo_path / 'dist' / '*'))
            
            if not dist_files:
                print(f"{Colors.RED}✗ No distribution files found in dist/{Colors.RESET}")
                return
            
            result = run_command(
                [sys.executable, '-m', 'twine', 'upload', *dist_files],
                cwd=repo_path,
                capture_output=False
            )
            if result.returncode == 0:
                print(f"{Colors.GREEN}✓ Published to PyPI{Colors.RESET}")
            else:
                print(f"{Colors.RED}✗ Upload failed{Colors.RESET}")


def handle_pypi_publishing(repo_path: Path, version: str, changelog: str, username: str):
    """
    Main entry point for PyPI publishing flow.
    
    Called from release.py after tagging.
    
    Args:
        repo_path: Path to repository
        version: Version being released (e.g., "v0.3.0")
        changelog: Changelog content for this release
        username: GitHub username
    """
    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}📦 PYPI PUBLISHING{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    
    # Get package name
    package_name = read_package_name(repo_path)
    if not package_name:
        print(f"{Colors.RED}✗ Could not read package name from pyproject.toml{Colors.RESET}")
        return
    
    print(f"\nPackage: {Colors.GREEN}{package_name}{Colors.RESET}")
    print(f"Version: {Colors.GREEN}{version}{Colors.RESET}")
    
    # Check PyPI status
    print(f"\n{Colors.CYAN}Checking PyPI status...{Colors.RESET}")
    pypi_status = check_pypi_status(package_name)
    
    if pypi_status == 'missing':
        print(f"{Colors.YELLOW}⚠ Package not found on PyPI (first release!){Colors.RESET}")
        first_release = True
    elif pypi_status == 'exists':
        print(f"{Colors.GREEN}✓ Package exists on PyPI{Colors.RESET}")
        first_release = False
    else:
        print(f"{Colors.YELLOW}⚠ Could not verify PyPI status (network issue?){Colors.RESET}")
        first_release = input(f"{Colors.CYAN}Is this the first release? (y/n):{Colors.RESET} ").strip().lower() == 'y'
    
    # Ensure workflow exists
    workflow_exists, method = ensure_publish_workflow(repo_path, package_name)
    
    # Guide setup for first release with OIDC
    if first_release and method == "oidc":
        guide_trusted_publisher_setup(repo_path, package_name, username)
    
    # Create GitHub release draft
    print(f"\n{Colors.CYAN}GitHub Release Options:{Colors.RESET}")
    print("  1. Create release draft (recommended - lets you edit before publishing)")
    print("  2. Skip GitHub release (handle manually)")
    
    create_release = input(f"\n{Colors.BRIGHT_BLUE}Choice (1-2):{Colors.RESET} ").strip()
    
    if create_release == "1":
        success = create_github_release_draft(repo_path, version, changelog, package_name)
        
        if success:
            # Offer to publish immediately
            print(f"\n{Colors.CYAN}📢 Release draft created!{Colors.RESET}")
            publish_now = input(f"{Colors.BRIGHT_BLUE}Publish release now? (y/n):{Colors.RESET} ").strip().lower()
            
            if publish_now == 'y':
                result = run_command(['gh', 'release', 'edit', version, '--draft=false'], cwd=repo_path, capture_output=False)
                if result.returncode == 0:
                    print(f"{Colors.GREEN}🚀 Release published! PyPI workflow triggered.{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}⚠ Could not publish. Run manually:{Colors.RESET}")
                    print(f"   gh release edit {version} --draft=false")
            else:
                print(f"\n{Colors.DIM}Publish later with:{Colors.RESET}")
                print(f"   gh release edit {version} --draft=false")
    
    # Publishing options
    print(f"\n{Colors.BOLD}Publishing Options:{Colors.RESET}")
    
    if workflow_exists:
        print(f"\n{Colors.CYAN}With GitHub Actions workflow:{Colors.RESET}")
        print("  • When you PUBLISH the GitHub release, the workflow will:")
        print("    1. Build the package")
        if method == "oidc":
            print("    2. Publish to PyPI automatically (via OIDC)")
        else:
            print("    2. Publish to PyPI automatically (via API token)")
        print(f"    3. Package will be live at: https://pypi.org/project/{package_name}/")
        print(f"\n{Colors.GREEN}✓ All set! Just publish the release when ready.{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}⚠ No workflow configured{Colors.RESET}")
        offer_manual_publish(repo_path)
    
    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.GREEN}PyPI publishing preparation complete!{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.RESET}\n")


def main():
    """Standalone entry point for testing."""
    repo_path = Path.cwd()
    
    # Test functionality
    package_name = read_package_name(repo_path)
    if package_name:
        print(f"Package: {package_name}")
        status = check_pypi_status(package_name)
        print(f"PyPI Status: {status}")
    else:
        print("No package name found")


if __name__ == "__main__":
    main()