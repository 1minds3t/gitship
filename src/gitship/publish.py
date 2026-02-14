#!/usr/bin/env python3
"""
publish - Publish local repository to GitHub with identity verification.

Handles:
- Multi-identity SSH configuration detection
- GitHub username verification
- Repository creation on GitHub
- Remote configuration with correct identity
- Initial push with branch settings
"""

import os
import sys
import subprocess
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple


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
    
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_CYAN = '\033[96m'


def run_command(args: List[str], capture_output: bool = True, check: bool = False, cwd: Path = None) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=check
    )


def parse_ssh_config() -> List[Dict[str, str]]:
    """
    Parse ~/.ssh/config to find GitHub SSH host configurations.
    
    Returns list of host configs with their identity files.
    """
    ssh_config_path = Path.home() / '.ssh' / 'config'
    
    if not ssh_config_path.exists():
        return []
    
    hosts = []
    current_host = {}
    
    try:
        with open(ssh_config_path, 'r') as f:
            for line in f:
                line = line.strip()
                
                if not line or line.startswith('#'):
                    continue
                
                if line.startswith('Host '):
                    # Save previous host
                    if current_host:
                        hosts.append(current_host)
                    # Start new host
                    host_name = line.split(None, 1)[1]
                    current_host = {'host': host_name}
                
                elif current_host:
                    if line.startswith('HostName'):
                        current_host['hostname'] = line.split(None, 1)[1]
                    elif line.startswith('User'):
                        current_host['user'] = line.split(None, 1)[1]
                    elif line.startswith('IdentityFile'):
                        identity = line.split(None, 1)[1]
                        # Expand ~ to home directory
                        if identity.startswith('~'):
                            identity = str(Path.home() / identity[2:])
                        current_host['identity_file'] = identity
                    elif line.startswith('IdentitiesOnly'):
                        current_host['identities_only'] = line.split(None, 1)[1].lower() == 'yes'
        
        # Don't forget the last host
        if current_host:
            hosts.append(current_host)
    
    except Exception as e:
        print(f"{Colors.YELLOW}Warning: Could not parse SSH config: {e}{Colors.RESET}")
        return []
    
    # Filter for GitHub hosts only
    github_hosts = []
    for host in hosts:
        hostname = host.get('hostname', '').lower()
        host_alias = host.get('host', '').lower()
        
        if 'github.com' in hostname or 'github' in host_alias:
            github_hosts.append(host)
    
    return github_hosts


def test_ssh_key(identity_file: str, host: str = "github.com") -> Optional[str]:
    """
    Test an SSH key and extract the GitHub username.
    
    Returns the GitHub username if successful, None otherwise.
    """
    try:
        # Expand path
        key_path = Path(identity_file).expanduser()
        
        if not key_path.exists():
            return None
        
        # Test SSH connection
        result = run_command([
            'ssh', '-T',
            '-i', str(key_path),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            f'git@{host}'
        ])
        
        # GitHub responds with: "Hi USERNAME! You've successfully authenticated..."
        output = result.stdout + result.stderr
        
        if 'Hi ' in output and '!' in output:
            # Extract username
            match = re.search(r'Hi ([^!]+)!', output)
            if match:
                return match.group(1)
        
        return None
    
    except Exception as e:
        return None


def get_git_config() -> Dict[str, str]:
    """Get git global configuration."""
    config = {}
    
    # Get user.name
    result = run_command(['git', 'config', '--global', 'user.name'])
    if result.returncode == 0:
        config['name'] = result.stdout.strip()
    
    # Get user.email
    result = run_command(['git', 'config', '--global', 'user.email'])
    if result.returncode == 0:
        config['email'] = result.stdout.strip()
    
    return config


def check_gh_cli() -> Optional[str]:
    """Check if GitHub CLI is installed and authenticated."""
    try:
        result = run_command(['gh', 'auth', 'status'])
        
        if result.returncode == 0:
            # Extract username from output
            output = result.stdout + result.stderr
            
            # Look for "Logged in to github.com as USERNAME"
            match = re.search(r'Logged in to github\.com as ([^\s]+)', output)
            if match:
                return match.group(1)
        
        return None
    
    except FileNotFoundError:
        return None


def get_current_branch(repo_path: Path) -> Optional[str]:
    """Get the current branch name."""
    result = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_path)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def verify_and_select_identity() -> Optional[Dict[str, str]]:
    """
    Verify user identity and let them select which GitHub account to use.
    
    Returns selected identity configuration or None if cancelled.
    """
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}🔍 IDENTITY VERIFICATION{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}\n")
    
    # Get git config
    git_config = get_git_config()
    
    print(f"{Colors.CYAN}Git Global Configuration:{Colors.RESET}")
    if git_config.get('name'):
        print(f"  Name:  {git_config['name']}")
    else:
        print(f"  Name:  {Colors.YELLOW}(not set){Colors.RESET}")
    
    if git_config.get('email'):
        print(f"  Email: {git_config['email']}")
    else:
        print(f"  Email: {Colors.YELLOW}(not set){Colors.RESET}")
    
    # Check GitHub CLI
    gh_user = check_gh_cli()
    if gh_user:
        print(f"\n{Colors.GREEN}✓ GitHub CLI authenticated as: {gh_user}{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}⚠ GitHub CLI not authenticated (gh auth login){Colors.RESET}")
    
    # Parse SSH config
    ssh_hosts = parse_ssh_config()
    
    if ssh_hosts:
        print(f"\n{Colors.CYAN}SSH GitHub Identities Found:{Colors.RESET}")
        
        # Test each SSH key to get username
        for i, host in enumerate(ssh_hosts, 1):
            print(f"\n  {i}. Host: {Colors.BRIGHT_CYAN}{host['host']}{Colors.RESET}")
            print(f"     HostName: {host.get('hostname', 'github.com')}")
            
            if 'identity_file' in host:
                print(f"     IdentityFile: {host['identity_file']}")
                
                # Test the key
                hostname = host.get('hostname', 'github.com')
                username = test_ssh_key(host['identity_file'], hostname)
                
                if username:
                    print(f"     {Colors.GREEN}✓ GitHub User: {username}{Colors.RESET}")
                    host['github_username'] = username
                else:
                    print(f"     {Colors.YELLOW}⚠ Could not verify GitHub user{Colors.RESET}")
            else:
                print(f"     {Colors.YELLOW}⚠ No IdentityFile specified{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}⚠ No GitHub SSH hosts found in ~/.ssh/config{Colors.RESET}")
    
    # Build options
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}Select Publishing Identity:{Colors.RESET}\n")
    
    options = []
    
    # Add SSH options
    for host in ssh_hosts:
        if 'github_username' in host:
            options.append({
                'type': 'ssh',
                'username': host['github_username'],
                'host_alias': host['host'],
                'hostname': host.get('hostname', 'github.com'),
                'identity_file': host['identity_file'],
                'ssh_url_format': f"git@{host['host']}"
            })
            print(f"  {len(options)}. {Colors.GREEN}{host['github_username']}{Colors.RESET} "
                  f"(SSH - {host['host']}, key: {Path(host['identity_file']).name})")
    
    # Add GitHub CLI option
    if gh_user:
        options.append({
            'type': 'gh_cli',
            'username': gh_user,
            'method': 'GitHub CLI (HTTPS)'
        })
        print(f"  {len(options)}. {Colors.GREEN}{gh_user}{Colors.RESET} (GitHub CLI - HTTPS)")
    
    # Manual option
    options.append({
        'type': 'manual',
        'username': None,
        'method': 'Manual Entry'
    })
    print(f"  {len(options)}. {Colors.DIM}Enter username manually{Colors.RESET}")
    
    if not options:
        print(f"{Colors.RED}No GitHub identities found!{Colors.RESET}")
        print(f"Please set up SSH keys or authenticate with GitHub CLI first.")
        return None
    
    # Get selection
    while True:
        try:
            choice = input(f"\n{Colors.BRIGHT_BLUE}Select option (1-{len(options)}):{Colors.RESET} ").strip()
            idx = int(choice) - 1
            
            if 0 <= idx < len(options):
                selected = options[idx]
                break
            else:
                print(f"{Colors.RED}Invalid selection. Please choose 1-{len(options)}{Colors.RESET}")
        except (ValueError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Cancelled{Colors.RESET}")
            return None
    
    # Handle manual entry
    if selected['type'] == 'manual':
        username = input(f"\n{Colors.CYAN}Enter GitHub username:{Colors.RESET} ").strip()
        if not username:
            print(f"{Colors.RED}Username required{Colors.RESET}")
            return None
        
        selected['username'] = username
        
        # Ask for authentication method
        print(f"\n{Colors.CYAN}Authentication method:{Colors.RESET}")
        print("  1. SSH")
        print("  2. HTTPS")
        
        auth_choice = input(f"{Colors.BRIGHT_BLUE}Select (1-2):{Colors.RESET} ").strip()
        
        if auth_choice == "1":
            selected['type'] = 'ssh'
            selected['ssh_url_format'] = 'git@github.com'
        else:
            selected['type'] = 'https'
    
    # Confirm selection
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}Confirmed Identity:{Colors.RESET}\n")
    print(f"  GitHub Username: {Colors.GREEN}{selected['username']}{Colors.RESET}")
    
    if selected['type'] == 'ssh':
        if 'host_alias' in selected:
            print(f"  SSH Host Alias: {Colors.CYAN}{selected['host_alias']}{Colors.RESET}")
            print(f"  SSH Key: {Colors.CYAN}{selected['identity_file']}{Colors.RESET}")
            print(f"  Remote URL Format: {Colors.DIM}git@{selected['host_alias']}:USERNAME/REPO.git{Colors.RESET}")
        else:
            print(f"  Method: SSH (default)")
            print(f"  Remote URL Format: {Colors.DIM}git@github.com:USERNAME/REPO.git{Colors.RESET}")
    elif selected['type'] == 'gh_cli':
        print(f"  Method: GitHub CLI (HTTPS)")
    else:
        print(f"  Method: HTTPS")
    
    confirm = input(f"\n{Colors.YELLOW}Publish with this identity? (y/n):{Colors.RESET} ").strip().lower()
    
    if confirm != 'y':
        print(f"{Colors.RED}Cancelled{Colors.RESET}")
        return None
    
    return selected


def create_github_repo(identity: Dict, repo_name: str, description: str = "", private: bool = False) -> bool:
    """
    Create a GitHub repository using GitHub CLI or API.
    
    Returns True if successful.
    """
    print(f"\n{Colors.CYAN}Creating GitHub repository...{Colors.RESET}")
    
    # Try with GitHub CLI first (easiest)
    try:
        args = ['gh', 'repo', 'create', repo_name]
        
        if private:
            args.append('--private')
        else:
            args.append('--public')
        
        if description:
            args.extend(['--description', description])
        
        # Don't clone, don't add remote yet (we'll do it manually with correct URL)
        args.append('--source=.')
        args.append('--remote=origin')
        
        result = run_command(args)
        
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Repository created on GitHub{Colors.RESET}")
            return True
        else:
            print(f"{Colors.RED}✗ Failed to create repository: {result.stderr}{Colors.RESET}")
            return False
    
    except FileNotFoundError:
        print(f"{Colors.YELLOW}⚠ GitHub CLI not found. Please install 'gh' or use HTTPS authentication{Colors.RESET}")
        return False


def configure_remote(identity: Dict, repo_name: str, repo_path: Path) -> bool:
    """
    Configure git remote with the correct URL based on identity.
    """
    username = identity['username']
    
    # Build remote URL based on authentication type
    if identity['type'] == 'ssh':
        if 'host_alias' in identity:
            # Use SSH config host alias
            remote_url = f"git@{identity['host_alias']}:{username}/{repo_name}.git"
        else:
            # Use default github.com
            remote_url = f"git@github.com:{username}/{repo_name}.git"
    else:
        # HTTPS
        remote_url = f"https://github.com/{username}/{repo_name}.git"
    
    print(f"\n{Colors.CYAN}Configuring remote...{Colors.RESET}")
    print(f"  Remote URL: {Colors.DIM}{remote_url}{Colors.RESET}")
    
    # Check if remote already exists
    result = run_command(['git', 'remote', 'get-url', 'origin'], cwd=repo_path)
    
    if result.returncode == 0:
        # Remote exists, update it
        result = run_command(['git', 'remote', 'set-url', 'origin', remote_url], cwd=repo_path)
    else:
        # Add new remote
        result = run_command(['git', 'remote', 'add', 'origin', remote_url], cwd=repo_path)
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Remote configured{Colors.RESET}")
        return True
    else:
        print(f"{Colors.RED}✗ Failed to configure remote{Colors.RESET}")
        return False


def push_to_remote(repo_path: Path, branch: str) -> bool:
    """Push branch to remote and set upstream."""
    print(f"\n{Colors.CYAN}Pushing to GitHub...{Colors.RESET}")
    
    result = run_command(
        ['git', 'push', '-u', 'origin', branch],
        cwd=repo_path,
        capture_output=False  # Show output to user
    )
    
    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Pushed branch '{branch}' to GitHub{Colors.RESET}")
        return True
    else:
        print(f"{Colors.RED}✗ Failed to push to GitHub{Colors.RESET}")
        return False

def get_multiline_description_editor() -> str:
    """Open editor for multiline description input."""
    import tempfile
    
    editor = os.environ.get('EDITOR', 'nano')
    
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False) as tf:
        # Start completely blank - no template noise
        tf.write("")
        tf.flush()
        temp_path = tf.name
    
    try:
        subprocess.run([editor, temp_path])
        
        with open(temp_path, 'r') as f:
            description = f.read().strip()
        
        return description
    
    except Exception as e:
        print(f"{Colors.YELLOW}Editor error: {e}{Colors.RESET}")
        return ""
    
    finally:
        try:
            os.unlink(temp_path)
        except:
            pass


def publish_repository(repo_path: Path):
    """
    Main publish workflow - interactive.
    """
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}🚀 PUBLISH REPOSITORY TO GITHUB{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    
    # Verify identity first
    identity = verify_and_select_identity()
    if not identity:
        return
    
    # Get repository information
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}Repository Configuration:{Colors.RESET}\n")
    
    # Suggest repo name from directory
    default_name = repo_path.name
    repo_name = input(f"{Colors.CYAN}Repository name [{default_name}]:{Colors.RESET} ").strip()
    if not repo_name:
        repo_name = default_name
    
    # Description
    print(f"\n{Colors.CYAN}Description (optional):{Colors.RESET}")
    print(f"  {Colors.DIM}Type a single line, or type 'EDIT' to open editor{Colors.RESET}")
    description_input = input(f"{Colors.CYAN}> {Colors.RESET}").strip()

    if description_input.upper() == 'EDIT':
        description = get_multiline_description_editor()
    else:
        description = description_input
        
    # Public or private
    visibility = input(f"{Colors.CYAN}Visibility (public/private) [public]:{Colors.RESET} ").strip().lower()
    private = visibility == 'private'
    
    # Get current branch
    current_branch = get_current_branch(repo_path)
    if not current_branch:
        print(f"{Colors.RED}Could not determine current branch{Colors.RESET}")
        return
    
    # Summary
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}Summary:{Colors.RESET}\n")
    print(f"  Repository: {Colors.GREEN}{identity['username']}/{repo_name}{Colors.RESET}")
    print(f"  Visibility: {Colors.CYAN}{'Private' if private else 'Public'}{Colors.RESET}")
    print(f"  Branch: {Colors.CYAN}{current_branch}{Colors.RESET}")
    if description:
        print(f"  Description: {description}")
    
    confirm = input(f"\n{Colors.YELLOW}Proceed with publish? (y/n):{Colors.RESET} ").strip().lower()
    if confirm != 'y':
        print(f"{Colors.RED}Cancelled{Colors.RESET}")
        return
    
    # Execute publish steps
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}Publishing...{Colors.RESET}\n")
    
    # Step 1: Create repo on GitHub
    if not create_github_repo(identity, repo_name, description, private):
        return
    
    # Step 2: Configure remote
    if not configure_remote(identity, repo_name, repo_path):
        return
    
    # Step 3: Push to GitHub
    if not push_to_remote(repo_path, current_branch):
        return
    
    # Success!
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.GREEN}{Colors.BOLD}🎉 SUCCESS!{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}\n")
    
    repo_url = f"https://github.com/{identity['username']}/{repo_name}"
    print(f"  Repository: {Colors.BRIGHT_CYAN}{repo_url}{Colors.RESET}")
    print(f"  Branch: {Colors.CYAN}{current_branch}{Colors.RESET}")
    print(f"\n{Colors.GREEN}Your repository is now live on GitHub!{Colors.RESET}\n")


def main_with_repo(repo_path: Path):
    """Entry point from gitship menu."""
    publish_repository(repo_path)


def main():
    """Standalone entry point."""
    repo_path = Path.cwd()
    publish_repository(repo_path)


if __name__ == "__main__":
    main()