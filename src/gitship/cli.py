#!/usr/bin/env python3
"""
gitship - Interactive Git history management CLI

Main entry point that provides a menu-driven interface or direct CLI commands
for various git operations.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional


try:
    from gitship import check, fix, review, release, commit, branch, publish
    from gitship.config import load_config, get_default_export_path
except ImportError:
    # For development/testing when not installed
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from gitship import check, fix, review, release, commit
    from gitship.config import load_config, get_default_export_path


def show_menu(repo_path: Path):
    """Display interactive menu for gitship operations."""
    print("\n" + "=" * 60)
    print("GITSHIP - Interactive Git History Manager")
    print("=" * 60)
    print(f"Repository: {repo_path}")
    print()
    print("Available Commands:")
    print("  1. check    - View recent commits and inspect changes")
    print("  2. fix      - Selectively restore files from commit history")
    print("  3. commit   - Smart commit with change analysis")
    print("  4. review   - Review changes between tags/commits with export")
    print("  5. release  - Bump version, changelog, and push release")
    print("  6. config   - View/edit gitship configuration")
    print("  7. branch   - Manage branches (create, switch, rename, set default)")
    print("  8. publish  - Create GitHub repo and push (with identity verification)")
    print("  9. deps     - Scan for and add missing dependencies to pyproject.toml")
    print("  0. exit     - Exit gitship")
    print()
    
    try:
        choice = input("Enter your choice (0-9): ").strip()
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)
    
    if choice == "1":
        check.main_with_repo(repo_path)
    elif choice == "2":
        commit_sha = input("Enter commit SHA (or press Enter to be prompted): ").strip()
        if commit_sha:
            fix.main_with_args(str(repo_path), commit_sha)
        else:
            fix.main_with_repo(repo_path)
    elif choice == "3":
        commit.main_with_repo(repo_path)
    elif choice == "4":
        review.main_with_repo(repo_path)
    elif choice == "5":
        release.main_with_repo(repo_path)
    elif choice == "6":
        config = load_config()
        print("\nCurrent Configuration:")
        print(f"  Export Path: {config.get('export_path', get_default_export_path())}")
        print(f"  Auto-push: {config.get('auto_push', True)}")
        print()
        print("Edit ~/.gitship/config.json to modify settings")
    elif choice == "7":
        from gitship import branch
        branch.main_with_repo(repo_path)
    elif choice == "8":
        from gitship import publish
        publish.main_with_repo(repo_path)
    elif choice == "9":
        from gitship import deps
        deps.main_with_repo(repo_path)
    elif choice == "0":
        print("Goodbye!")
        sys.exit(0)
    else:
        print(f"Invalid choice: {choice}")
        sys.exit(1)


def main():
    """Main entry point for gitship CLI."""
    parser = argparse.ArgumentParser(
        description="gitship - Interactive Git history management and commit inspection tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gitship                          # Interactive menu in current directory
  gitship --menu                   # Interactive menu in current directory
  gitship check                    # Run check in current directory
  gitship fix a1b2c3d              # Restore files from before commit a1b2c3d
  gitship review                   # Review changes between HEAD and last tag
  gitship review --from v1.0.0 --to v2.0.0  # Review changes between tags
  gitship review --export          # Export full diff to file
  gitship -r ~/myproject check     # Run check in specific repo

Commands:
  check      - View recent commits, inspect changes, and revert
  fix        - Selectively restore files from commit history
  review     - Review changes between tags/commits with export options
  release    - Interactive release creator
  commit     - Smart commit with change analysis
  branch     - Interactive branch management
  publish    - Publish repository to GitHub
  deps       - Scan and add missing dependencies
  config     - View configuration settings
        """
    )
    
    parser.add_argument(
        '-r', '--repo',
        type=str,
        default=None,
        help='Path to git repository (default: current directory)'
    )
    
    parser.add_argument(
        '--menu',
        action='store_true',
        help='Show interactive menu (default if no command specified)'
    )
    
    parser.add_argument(
        '-v', '--version',
        action='version',
        version='gitship 0.3.0'  # Updated from 0.2.0
    )
    
    # Subcommands
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
        
        # check subcommand
    check_parser = subparsers.add_parser(
        'check',
        help='View recent commits and inspect changes'
    )
    check_parser.add_argument(
        '-n', '--count',
        type=int,
        default=10,
        help='Number of commits to show (default: 10)'
    )

    # fix subcommand
    fix_parser = subparsers.add_parser(
        'fix',
        help='Selectively restore files from commit history'
    )
    fix_parser.add_argument(
        'commit',
        nargs='?',
        help='Commit SHA to restore files from (before this commit)'
    )
    commit_parser = subparsers.add_parser(
        'commit',
        help='Smart commit with change analysis'
    )
    commit_parser.add_argument(
        '--message', '-m',
        type=str,
        help='Commit message (skip interactive prompt)'
    )
    # review subcommand
    review_parser = subparsers.add_parser(
        'review',
        help='Review changes between tags/commits with export options'
    )
    review_parser.add_argument(
        '--from',
        dest='from_ref',
        type=str,
        help='Starting reference (tag/commit/branch). Default: last tag'
    )
    review_parser.add_argument(
        '--to',
        dest='to_ref',
        type=str,
        default='HEAD',
        help='Ending reference (tag/commit/branch). Default: HEAD'
    )
    review_parser.add_argument(
        '--export',
        action='store_true',
        help='Export full diff to file'
    )
    review_parser.add_argument(
        '--export-path',
        type=str,
        help='Custom export path (default: from config or ~/omnipkg_git_cleanup)'
    )
    review_parser.add_argument(
        '--stat-only',
        action='store_true',
        help='Show only diff stats, not full commit messages'
    )
    
    # config subcommand
    config_parser = subparsers.add_parser(
        'config',
        help='View or edit configuration'
    )
    config_parser.add_argument(
        '--show',
        action='store_true',
        help='Show current configuration'
    )
    config_parser.add_argument(
        '--set-export-path',
        type=str,
        help='Set default export path for diffs'
    )

    # branch subcommand  
    branch_parser = subparsers.add_parser(
        'branch',
        help='Manage branches (create, switch, rename, delete, set default)'
    )
    branch_parser.add_argument(
        'operation',
        nargs='?',
        choices=['list', 'create', 'switch', 'rename', 'delete', 'set-default'],
        help='Branch operation to perform'
    )
    branch_parser.add_argument(
        '--name',
        type=str,
        help='Branch name for create/switch/delete operations'
    )
    branch_parser.add_argument(
        '--from',
        dest='from_ref',
        type=str,
        help='Starting point for new branch (create operation)'
    )
    branch_parser.add_argument(
        '--old-name',
        type=str,
        help='Old branch name (rename operation)'
    )
    branch_parser.add_argument(
        '--new-name',
        type=str,
        help='New branch name (rename operation)'
    )
    branch_parser.add_argument(
        '--force',
        action='store_true',
        help='Force delete unmerged branch'
    )
    branch_parser.add_argument(
        '--remote',
        action='store_true',
        help='Also update remote (for rename/set-default)'
    )
    branch_parser.add_argument(
        '--switch',
        action='store_true',
        help='Switch to branch after creating'
    )
    branch_parser.add_argument(
        '--show-remote',
        action='store_true',
        help='Show remote branches (list operation)'
    )
    # publish subcommand
    publish_parser = subparsers.add_parser(
        'publish',
        help='Publish repository to GitHub with identity verification'
    )
    publish_parser.add_argument(
        '--name',
        type=str,
        help='Repository name (default: directory name)'
    )
    publish_parser.add_argument(
        '--description',
        type=str,
        help='Repository description'
    )
    publish_parser.add_argument(
        '--private',
        action='store_true',
        help='Create private repository (default: public)'
    )
    publish_parser.add_argument(
        '--identity',
        type=str,
        help='GitHub username to publish as (skip interactive selection)'
    )
    # deps subcommand
    deps_parser = subparsers.add_parser(
        'deps',
        help='Scan for and add missing dependencies to pyproject.toml'
    )
    args = parser.parse_args()
    
    # Determine repository path
    if args.repo:
        repo_path = Path(args.repo).resolve()
    else:
        repo_path = Path.cwd()
    
    # Validate repository
    if not check.is_git_repo(repo_path):
        print(f"Error: Not in a git repository: {repo_path}", file=sys.stderr)
        sys.exit(1)
    
    # Handle commands
    if args.command == 'check':
        check.main_with_args(str(repo_path), count=args.count)
    
    elif args.command == 'fix':
        if args.commit:
            fix.main_with_args(str(repo_path), args.commit)
        else:
            fix.main_with_repo(repo_path)
    
    elif args.command == 'commit':
        commit.main_with_repo(repo_path)
    
    elif args.command == 'review':
        review.main_with_args(
            repo_path=repo_path,
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            export=args.export,
            export_path=args.export_path,
            stat_only=args.stat_only
        )
    
    elif args.command == 'config':
        from gitship.config import show_config, set_export_path
        if args.set_export_path:
            set_export_path(args.set_export_path)
        else:
            show_config()
    elif args.command == 'branch':
        from gitship import branch
        if args.operation:
            branch.main_with_args(
                repo_path=str(repo_path),
                operation=args.operation,
                name=args.name,
                from_ref=args.from_ref,
                old_name=args.old_name,
                new_name=args.new_name,
                force=args.force,
                update_remote=args.remote,
                switch=args.switch,
                show_remote=args.show_remote
            )
        else:
            # No operation, show interactive menu
            branch.main_with_repo(repo_path)
    elif args.command == 'publish':
        from gitship import publish
        publish.publish_repository(repo_path)
    elif args.command == 'deps':
        from gitship import deps
        deps.main_with_repo(repo_path)
        
    else:
        # No command specified, show menu
        show_menu(repo_path)


if __name__ == "__main__":
    main()