#!/usr/bin/env python3
"""
gitship - Interactive Git history management CLI

Main entry point that provides a menu-driven interface or direct CLI commands
for various git operations.
"""

import argparse
import sys
import textwrap
from pathlib import Path
from typing import Optional


try:
    from gitship import check, fix, review, release, commit, branch, publish, docs, sync, amend, init, vscode_history
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
    print("  1. check         - View recent commits and inspect changes")
    print("  2. fix           - Selectively restore files from commit history")
    print("  3. commit        - Smart commit with change analysis")
    print("  4. review        - Review changes between tags/commits with export")
    print("  5. release       - Bump version, changelog, and push release")
    print("  6. config        - View/edit gitship configuration")
    print("  7. branch        - Manage branches (create, switch, rename, set default)")
    print("  8. publish       - Create GitHub repo and push (with identity verification)")
    print("  9. deps          - Scan for and add missing dependencies to pyproject.toml")
    print("  10. docs         - Generate or update README.md")
    print("  11. resolve      - Interactive merge conflict resolver")
    print("  12. merge        - Merge branches interactively")
    print("  13. pull         - Pull changes from remote (with rebase)")
    print("  14. push         - Push changes to remote")
    print("  15. sync         - Pull and push in one operation")
    print("  16. amend        - Amend last commit with smart message")
    print("  17. ignore       - Manage .gitignore entries")
    print("  18. licenses     - Fetch license files for dependencies")
    print("  19. init         - Initialize a new git repository")
    print("  20. vscode-history - Restore files from VSCode local history")
    print("  21. ci           - GitHub Actions: observe, create, edit, trigger workflows")
    print("  0. exit          - Exit gitship")
    print()
    
    try:
        choice = input("Enter your choice (0-21): ").strip()
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
        print("  1. Interactive editor (edit sections)")
        print("  2. Generate default README")
        print("  3. Update from file")
        sub = input("Choice (1-3): ").strip()
        if sub == "1":
            docs.main_with_args(repo_path, edit=True)
        elif sub == "2":
            docs.main_with_args(repo_path, generate=True)
        elif sub == "3":
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
    elif choice == "17":
        from gitship import gitignore
        gitignore.interactive_gitignore(repo_path)
    elif choice == "18":
        from gitship import licenses
        licenses.interactive_licenses(repo_path)
    elif choice == "19":
        from gitship import init
        init.main_with_repo(repo_path)
    elif choice == "20":
        from gitship import vscode_history
        vscode_history.main_with_repo(repo_path)
    elif choice == "21":
        from gitship import ci
        ci.main_with_repo(repo_path)
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
  gitship vscode-history           # Restore files from VSCode local history
  gitship vscode-history --list    # List files with available history
  gitship vscode-history --dry-run # Preview what would be restored

Commands:
  check          - View recent commits, inspect changes, and revert
  fix            - Selectively restore files from commit history
  commit         - Smart commit with change analysis
  review         - Review changes between tags/commits with export options
  release        - Bump version, changelog, tag and push a release
  config         - View configuration settings
  branch         - Interactive branch management
  publish        - Publish repository to GitHub
  deps           - Scan and add missing dependencies
  docs           - Generate or update README.md
  resolve        - Interactive merge conflict resolver
  merge          - Merge branches interactively
  pull           - Pull changes from remote (with rebase)
  push           - Push changes to remote
  sync           - Pull and push in one operation
  amend          - Amend last commit with smart message
  ignore         - Manage .gitignore entries
  licenses       - Fetch license files for dependencies
  init           - Initialize or repair a git repository
  vscode-history - Restore files from VSCode local edit history
  ci             - CI/CD observability and management (GitHub Actions, GitLab, Jenkins)
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
        version='gitship 0.3.0'
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

    # commit subcommand
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
    
    # release subcommand
    release_parser = subparsers.add_parser(
        'release',
        help='Bump version, generate changelog, tag and push a release'
    )
    release_parser.add_argument(
        'bump', nargs='?', choices=['major', 'minor', 'patch'],
        help='Version bump type (interactive if not specified)'
    )
    release_parser.add_argument(
        '--version', '-V', type=str, metavar='VERSION',
        help='Set an explicit version string instead of bumping (e.g. 1.4.0)'
    )
    release_parser.add_argument(
        '--dry-run', action='store_true',
        help='Preview what would happen without making any changes'
    )
    release_parser.add_argument(
        '--no-push', action='store_true',
        help='Tag and commit locally but do not push to remote'
    )
    release_parser.add_argument(
        '--no-tag', action='store_true',
        help='Bump version and commit but skip creating a git tag'
    )
    release_parser.add_argument(
        '--message', '-m', type=str,
        help='Custom release/tag message'
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
    branch_parser.add_argument('--name', type=str, help='Branch name')
    branch_parser.add_argument('--from', dest='from_ref', type=str,
                               help='Starting point for new branch')
    branch_parser.add_argument('--old-name', type=str, help='Old branch name (rename)')
    branch_parser.add_argument('--new-name', type=str, help='New branch name (rename)')
    branch_parser.add_argument('--force', action='store_true',
                               help='Force delete unmerged branch')
    branch_parser.add_argument('--remote', action='store_true',
                               help='Also update remote (rename/set-default)')
    branch_parser.add_argument('--switch', action='store_true',
                               help='Switch to branch after creating')
    branch_parser.add_argument('--show-remote', action='store_true',
                               help='Show remote branches (list)')

    # publish subcommand
    publish_parser = subparsers.add_parser(
        'publish',
        help='Publish repository to GitHub with identity verification'
    )
    publish_parser.add_argument('--name', type=str,
                                help='Repository name (default: directory name)')
    publish_parser.add_argument('--description', type=str,
                                help='Repository description')
    publish_parser.add_argument('--private', action='store_true',
                                help='Create private repository (default: public)')
    publish_parser.add_argument('--identity', type=str,
                                help='GitHub username to publish as')

    # deps subcommand
    deps_parser = subparsers.add_parser(
        'deps',
        help='Scan for and add missing dependencies to pyproject.toml'
    )
    deps_parser.add_argument('--list-ignored', action='store_true',
                             help='List packages in permanent ignore list')
    deps_parser.add_argument('--add-ignore', type=str, metavar='PACKAGE',
                             help='Add a package to permanent ignore list')
    deps_parser.add_argument('--remove-ignore', type=str, metavar='PACKAGE',
                             help='Remove a package from permanent ignore list')

    # docs subcommand
    docs_parser = subparsers.add_parser('docs', help='Manage documentation/README')
    docs_parser.add_argument('--edit', action='store_true',
                             help='Interactive section-by-section README editor')
    docs_parser.add_argument('--generate', action='store_true',
                             help='Generate default README with current features')
    docs_parser.add_argument('--source', type=str, help='Update README from source file')
    
    # resolve subcommand
    subparsers.add_parser('resolve', help='Interactive merge conflict resolver')
    
    # merge subcommand
    merge_parser = subparsers.add_parser('merge', help='Merge branches interactively')
    merge_parser.add_argument('branch', nargs='?',
                              help='Branch to merge (interactive if not specified)')
    merge_parser.add_argument('--strategy', choices=['ours', 'theirs'],
                              help='Merge strategy for conflicts')
    merge_parser.add_argument('--no-commit', action='store_true',
                              help='Merge but do not auto-commit')
    
    # pull subcommand
    pull_parser = subparsers.add_parser('pull', help='Pull changes from remote (with rebase)')
    pull_parser.add_argument('--merge', action='store_true',
                             help='Use merge instead of rebase')
    pull_parser.add_argument('--branch', help='Specific branch to pull')
    
    # push subcommand
    push_parser = subparsers.add_parser('push', help='Push changes to remote')
    push_parser.add_argument('--force', action='store_true',
                             help='Force push (use with caution!)')
    push_parser.add_argument('--set-upstream', action='store_true',
                             help='Set upstream tracking branch')
    
    # sync subcommand
    sync_parser = subparsers.add_parser('sync', help='Pull and push in one operation')
    sync_parser.add_argument('--merge', action='store_true',
                             help='Use merge instead of rebase for pull')
    
    # amend subcommand
    amend_parser = subparsers.add_parser('amend', help='Amend last commit with smart message generation')
    amend_parser.add_argument('--auto', action='store_true',
                              help='Automatically use smart message without prompting')
    
    # ignore subcommand
    ignore_parser = subparsers.add_parser('ignore', help='Manage .gitignore entries')
    ignore_parser.add_argument('--add', type=str, metavar='PATTERN',
                               help='Add pattern to .gitignore')
    ignore_parser.add_argument('--remove', type=str, metavar='PATTERN',
                               help='Remove pattern from .gitignore')
    ignore_parser.add_argument('--list', action='store_true', dest='list_ignore',
                               help='List current .gitignore entries')
    ignore_parser.add_argument('--common', type=str, choices=['python', 'node'],
                               help='Add common patterns for language')
    
    # licenses subcommand
    licenses_parser = subparsers.add_parser('licenses',
                                            help='Fetch license files for project dependencies')
    licenses_parser.add_argument('--fetch', action='store_true',
                                 help='Fetch licenses for all dependencies in pyproject.toml')
    licenses_parser.add_argument('--list', action='store_true', dest='list_licenses',
                                 help='List current license files')

    # init subcommand
    subparsers.add_parser(
        'init',
        help='Initialize a new git repository (or repair a corrupted one)'
    )

    # vscode-history subcommand
    vsc_parser = subparsers.add_parser(
        'vscode-history',
        help='Restore files from VSCode local edit history'
    )
    vsc_parser.add_argument(
        'directory', nargs='?', default=None,
        help='Directory to restore (default: current repo or working directory)'
    )
    vsc_parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be restored without writing anything'
    )
    vsc_parser.add_argument(
        '--list', action='store_true', dest='list_history',
        help='List files with available VSCode history and exit'
    )
    vsc_parser.add_argument(
        '--no-backup', action='store_true',
        help="Don't create .bak files before overwriting"
    )

    # ci subcommand
    ci_parser = subparsers.add_parser(
        'ci',
        help='GitHub Actions workflow observability and management',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            GitHub Actions CI — observe, create, trigger and manage workflows.

            Observability:
              gitship ci                         # Interactive CI menu
              gitship ci --overview              # All workflows + stats
              gitship ci --runs                  # Recent runs (all)
              gitship ci --runs --workflow NAME  # Runs for one workflow
              gitship ci --events                # Event → workflow map
              gitship ci --errors RUN_ID         # Failure logs for a run

            Actions:
              gitship ci --trigger NAME          # Dispatch a workflow
              gitship ci --trigger NAME --branch develop
              gitship ci --rerun RUN_ID          # Rerun failed jobs
              gitship ci --rerun RUN_ID --all    # Rerun all jobs
              gitship ci --cancel RUN_ID         # Cancel a run

            Manage workflow files:
              gitship ci --create                # Create from template
              gitship ci --create --template python-test --filename test.yml
              gitship ci --edit                  # Edit in $EDITOR
              gitship ci --edit --filename test.yml
              gitship ci --delete --filename old.yml
              gitship ci --triggers              # Edit events/cron
              gitship ci --triggers --filename ci.yml

            Templates: python-test, python-lint, release-pypi,
                       scheduled-job, docker-build, blank
        """),
    )
    # Observability flags
    ci_parser.add_argument('--overview', action='store_true',
                           help='Show all workflows with run stats')
    ci_parser.add_argument('--runs', action='store_true',
                           help='Show recent workflow runs')
    ci_parser.add_argument('--events', action='store_true',
                           help='Show event → workflow map')
    ci_parser.add_argument('--errors', metavar='RUN_ID',
                           help='Show failure logs for a run ID')
    ci_parser.add_argument('--workflow', metavar='NAME',
                           help='Filter runs by workflow name')
    ci_parser.add_argument('--limit', type=int, default=20, metavar='N',
                           help='Number of runs to show (default: 20)')
    # Action flags
    ci_parser.add_argument('--trigger', metavar='WORKFLOW',
                           help='Manually dispatch a workflow (needs workflow_dispatch trigger)')
    ci_parser.add_argument('--rerun', metavar='RUN_ID',
                           help='Rerun failed jobs in a run (add --all for all jobs)')
    ci_parser.add_argument('--all', dest='rerun_all', action='store_true',
                           help='Rerun all jobs (use with --rerun)')
    ci_parser.add_argument('--cancel', metavar='RUN_ID',
                           help='Cancel an in-progress run')
    ci_parser.add_argument('--branch', default='main',
                           help='Branch to use when triggering (default: main)')
    # File management flags
    ci_parser.add_argument('--create', action='store_true',
                           help='Create a new workflow file from template')
    ci_parser.add_argument('--template', metavar='NAME',
                           help='Template to use with --create')
    ci_parser.add_argument('--filename', metavar='FILE',
                           help='Workflow filename for --create/--edit/--delete/--triggers')
    ci_parser.add_argument('--edit', action='store_true',
                           help='Edit a workflow file in $EDITOR')
    ci_parser.add_argument('--delete', action='store_true',
                           help='Delete a workflow file')
    ci_parser.add_argument('--triggers', action='store_true',
                           help='Edit trigger events / cron schedule for a workflow')
    ci_parser.add_argument('--dry-run', action='store_true', dest='ci_dry_run',
                           help='Preview changes as a diff without writing any files')
    ci_parser.add_argument('--json', action='store_true', dest='ci_json',
                           help='Output results as JSON (for overview, runs, events, errors)')

    args = parser.parse_args()
    
    # Determine repository path
    if args.repo:
        repo_path = Path(args.repo).resolve()
    else:
        repo_path = Path.cwd()

    # ── Commands that bypass the git-repo check ────────────────────────────────

    if args.command == 'init':
        from gitship import init
        init.main_with_repo(repo_path)
        return

    if args.command == 'vscode-history':
        from gitship import vscode_history
        target = Path(args.directory).resolve() if args.directory else repo_path

        restorer = vscode_history.VSCodeHistory(target_dir=target)
        restorer.scan()

        if args.list_history:
            if not restorer.file_versions:
                print("  No VSCode history found for this directory.")
                return
            print()
            for rel_path, versions in sorted(restorer.file_versions.items()):
                latest = versions[0]["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                print(f"  📄 {rel_path}  ({len(versions)} versions, latest: {latest})")
            return

        restorer.interactive_restore(
            backup=not args.no_backup,
            dry_run=args.dry_run,
        )
        return

    # ── All other commands require a valid git repo ────────────────────────────

    if not check.is_git_repo(repo_path):
        print(f"Error: Not in a git repository: {repo_path}", file=sys.stderr)
        print("Tip: Run 'gitship init' to set up a new repository here.", file=sys.stderr)
        sys.exit(1)

    # Silently keep gitship's own directories out of the repo's history
    try:
        from gitship import gitignore as _gitignore
        _gitignore.ensure_self_ignored(repo_path)
    except Exception:
        pass  # Never block the user over this

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
            branch.main_with_repo(repo_path)

    elif args.command == 'publish':
        from gitship import publish
        publish.publish_repository(repo_path)

    elif args.command == 'deps':
        from gitship import deps
        from gitship.config import (list_ignored_dependencies_for_project,
                                    add_ignored_dependency,
                                    remove_ignored_dependency)
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
        docs.main_with_args(repo_path, source=args.source,
                            generate=args.generate, edit=args.edit)
    
    elif args.command == 'resolve':
        from gitship import resolve_conflicts
        resolve_conflicts.main()
    
    elif args.command == 'merge':
        from gitship import merge
        if args.branch:
            strategy = args.strategy if hasattr(args, 'strategy') else None
            merge.merge_branch(repo_path, args.branch, strategy)
        else:
            merge.main_with_repo(repo_path)
    
    elif args.command == 'release':
        import inspect as _inspect
        _sig = _inspect.signature(release.main_with_repo)
        _kwargs = {}
        if 'bump'       in _sig.parameters: _kwargs['bump']       = getattr(args, 'bump', None)
        if 'version'    in _sig.parameters: _kwargs['version']    = getattr(args, 'version', None)
        if 'dry_run'    in _sig.parameters: _kwargs['dry_run']    = getattr(args, 'dry_run', False)
        if 'no_push'    in _sig.parameters: _kwargs['no_push']    = getattr(args, 'no_push', False)
        if 'no_tag'     in _sig.parameters: _kwargs['no_tag']     = getattr(args, 'no_tag', False)
        if 'message'    in _sig.parameters: _kwargs['message']    = getattr(args, 'message', None)
        release.main_with_repo(repo_path, **_kwargs)

    elif args.command in ['pull', 'push', 'sync']:
        import inspect as _inspect
        _sig = _inspect.signature(sync.main_with_repo)
        _kwargs = {}
        if 'use_merge'    in _sig.parameters: _kwargs['use_merge']    = getattr(args, 'merge', False)
        if 'force'        in _sig.parameters: _kwargs['force']        = getattr(args, 'force', False)
        if 'set_upstream' in _sig.parameters: _kwargs['set_upstream'] = getattr(args, 'set_upstream', False)
        if 'branch'       in _sig.parameters: _kwargs['branch']       = getattr(args, 'branch', None)
        sync.main_with_repo(repo_path, args.command, **_kwargs)
    
    elif args.command == 'amend':
        amend.main_with_repo(repo_path)
    
    elif args.command == 'ignore':
        from gitship import gitignore
        gitignore.main_with_args(
            repo_path,
            add=args.add,
            remove=args.remove,
            list_entries=args.list_ignore,
            common=args.common
        )
    
    elif args.command == 'licenses':
        from gitship import licenses
        licenses.main_with_args(
            repo_path,
            fetch=args.fetch,
            list_files=args.list_licenses
        )

    elif args.command == 'ci':
        from gitship import ci
        ci.main_with_args(
            repo_path,
            overview=args.overview,
            runs=args.runs,
            events=args.events,
            errors=args.errors,
            workflow=args.workflow,
            limit=args.limit,
            trigger=args.trigger,
            rerun=args.rerun,
            rerun_all_flag=args.rerun_all,
            cancel=args.cancel,
            branch=args.branch,
            create=args.create,
            template=args.template,
            filename=args.filename,
            edit=args.edit,
            delete=args.delete,
            triggers=args.triggers,
            dry_run=args.ci_dry_run,
            as_json=args.ci_json,
        )
        
    else:
        # No command specified, show menu
        show_menu(repo_path)


if __name__ == "__main__":
    main()