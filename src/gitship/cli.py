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
    from gitship import check, fix, review, release, commit, branch, publish, docs, sync, amend
    from gitship.config import load_config, get_default_export_path
except ImportError:
    # For development/testing when not installed
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from gitship import check, fix, review, release, commit, docs, sync, amend
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
    print("  10. docs    - Generate or update README.md")
    print("  11. resolve - Interactive merge conflict resolver")
    print("  12. merge   - Merge branches interactively")
    print("  13. pull    - Pull changes from remote (with rebase)")
    print("  14. push    - Push changes to remote")
    print("  15. sync    - Pull and push in one operation")
    print("  16. amend   - Amend last commit with smart message")
    print("  0. exit     - Exit gitship")
    print()
    
    try:
        choice = input("Enter your choice (0-16): ").strip()
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
        print("\nDependency Management:")
        print("  1. Scan and add missing dependencies")
        print("  2. List permanently ignored packages")
        print("  3. Add package to ignore list")
        print("  4. Remove package from ignore list")
        sub = input("Choice (1-4): ").strip()
        
        if sub == "1":
            deps.main_with_repo(repo_path)
        elif sub == "2":
            from gitship.config import list_ignored_dependencies_for_project
            list_ignored_dependencies_for_project(repo_path)
            input("\nPress Enter to continue...")
        elif sub == "3":
            pkg = input("Package name to ignore: ").strip()
            if pkg:
                from gitship.config import add_ignored_dependency
                add_ignored_dependency(pkg, repo_path)
                input("\nPress Enter to continue...")
        elif sub == "4":
            from gitship.config import get_ignored_dependencies, remove_ignored_dependency
            ignored = get_ignored_dependencies(repo_path)
            if not ignored:
                print("\n⚠️  No packages in ignore list for this project")
                input("\nPress Enter to continue...")
            else:
                print(f"\nCurrently ignored:")
                for i, p in enumerate(sorted(ignored), 1):
                    print(f"  {i}. {p}")
                pkg = input("\nPackage name or number to unignore: ").strip()
                if pkg.isdigit():
                    idx = int(pkg) - 1
                    if 0 <= idx < len(ignored):
                        pkg = sorted(ignored)[idx]
                if pkg in ignored:
                    remove_ignored_dependency(pkg, repo_path)
                    input("\nPress Enter to continue...")
    elif choice == "10":
        from gitship import docs
        print("\nDocs Options:")
        print("  1. Generate default README")
        print("  2. Update from file")
        sub = input("Choice (1-2): ").strip()
        if sub == "1":
            docs.main_with_args(repo_path, generate=True)
        elif sub == "2":
            src = input("Source file path: ").strip()
            if src:
                docs.main_with_args(repo_path, source=src)
    elif choice == "11":
        from gitship import resolve_conflicts
        resolve_conflicts.main()
    elif choice == "12":
        from gitship import merge
        merge.main_with_repo(repo_path)
    elif choice == "13":
        sync.main_with_repo(repo_path, "pull")
    elif choice == "14":
        sync.main_with_repo(repo_path, "push")
    elif choice == "15":
        sync.main_with_repo(repo_path, "sync")
    elif choice == "16":
        amend.main_with_repo(repo_path)
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
    deps_parser.add_argument(
        '--list-ignored',
        action='store_true',
        help='List packages in permanent ignore list for this project'
    )
    deps_parser.add_argument(
        '--add-ignore',
        type=str,
        metavar='PACKAGE',
        help='Add a package to permanent ignore list'
    )
    deps_parser.add_argument(
        '--remove-ignore',
        type=str,
        metavar='PACKAGE',
        help='Remove a package from permanent ignore list'
    )
    # docs subcommand
    docs_parser = subparsers.add_parser(
        'docs',
        help='Manage documentation/README'
    )
    docs_parser.add_argument(
        '--generate',
        action='store_true',
        help='Generate default README with current features'
    )
    docs_parser.add_argument(
        '--source',
        type=str,
        help='Update README from source file'
    )
    
    # resolve subcommand
    resolve_parser = subparsers.add_parser(
        'resolve',
        help='Interactive merge conflict resolver'
    )
    
    # merge subcommand
    merge_parser = subparsers.add_parser(
        'merge',
        help='Merge branches interactively'
    )
    merge_parser.add_argument(
        'branch',
        nargs='?',
        help='Branch to merge (interactive if not specified)'
    )
    merge_parser.add_argument(
        '--strategy',
        choices=['ours', 'theirs'],
        help='Merge strategy for conflicts'
    )
    merge_parser.add_argument(
        '--no-commit',
        action='store_true',
        help='Merge but do not auto-commit'
    )
    
    # Pull command
    pull_parser = subparsers.add_parser(
        'pull',
        help='Pull changes from remote (with rebase)'
    )
    pull_parser.add_argument(
        '--merge',
        action='store_true',
        help='Use merge instead of rebase'
    )
    pull_parser.add_argument(
        '--branch',
        help='Specific branch to pull'
    )
    
    # Push command
    push_parser = subparsers.add_parser(
        'push',
        help='Push changes to remote'
    )
    push_parser.add_argument(
        '--force',
        action='store_true',
        help='Force push (use with caution!)'
    )
    push_parser.add_argument(
        '--set-upstream',
        action='store_true',
        help='Set upstream tracking branch'
    )
    
    # Sync command
    sync_parser = subparsers.add_parser(
        'sync',
        help='Pull and push in one operation'
    )
    sync_parser.add_argument(
        '--merge',
        action='store_true',
        help='Use merge instead of rebase for pull'
    )
    
    # Amend command
    amend_parser = subparsers.add_parser(
        'amend',
        help='Amend last commit with smart message generation'
    )
    amend_parser.add_argument(
        '--auto',
        action='store_true',
        help='Automatically use smart message without prompting'
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
        from gitship.config import list_ignored_dependencies_for_project, add_ignored_dependency, remove_ignored_dependency
        
        if args.list_ignored:
            list_ignored_dependencies_for_project(repo_path)
        elif args.add_ignore:
            add_ignored_dependency(args.add_ignore, repo_path)
            print(f"✓ Added '{args.add_ignore}' to permanent ignore list for this project")
        elif args.remove_ignore:
            remove_ignored_dependency(args.remove_ignore, repo_path)
        else:
            deps.main_with_repo(repo_path)
    elif args.command == 'docs':
        from gitship import docs
        docs.main_with_args(repo_path, source=args.source, generate=args.generate)
    
    elif args.command == 'resolve':
        from gitship import resolve_conflicts
        resolve_conflicts.main()
    
    elif args.command == 'merge':
        from gitship import merge
        if args.branch:
            # Direct merge
            strategy = args.strategy if hasattr(args, 'strategy') else None
            merge.merge_branch(repo_path, args.branch, strategy)
        else:
            # Interactive
            merge.main_with_repo(repo_path)
    
    elif args.command in ['pull', 'push', 'sync']:
        sync.main_with_repo(repo_path, args.command)
    
    elif args.command == 'amend':
        amend.main_with_repo(repo_path)
        
    else:
        # No command specified, show menu
        show_menu(repo_path)


if __name__ == "__main__":
    main()