#!/usr/bin/env python3
"""
tag - Git tag management for gitship.

Provides full tag lifecycle:
- List local and remote tags with metadata
- Create annotated or lightweight tags
- Push tags to remote (one, all, or by pattern)
- Fetch/pull tags from remote
- Delete tags (local, remote, or both)
- Show tag details (message, commit, date)
"""

import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class UserCancelled(Exception):
    pass


def _sigint_handler(sig, frame):
    raise UserCancelled()


def safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        raise UserCancelled()


class Colors:
    RESET        = '\033[0m'
    BOLD         = '\033[1m'
    DIM          = '\033[2m'
    RED          = '\033[31m'
    GREEN        = '\033[32m'
    YELLOW       = '\033[33m'
    CYAN         = '\033[36m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_CYAN  = '\033[96m'


def run_git(args: List[str], repo_path: Path, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=check,
        encoding="utf-8",
        errors="replace",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_remotes(repo_path: Path) -> List[str]:
    r = run_git(["remote"], repo_path)
    return [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]


def _local_tags(repo_path: Path) -> List[str]:
    r = run_git(["tag", "--sort=-creatordate"], repo_path)
    return [t.strip() for t in r.stdout.strip().splitlines() if t.strip()]


def _remote_tags(repo_path: Path, remote: str) -> List[str]:
    r = run_git(["ls-remote", "--tags", "--refs", remote], repo_path)
    tags = []
    for line in r.stdout.strip().splitlines():
        if "\trefs/tags/" in line:
            tags.append(line.split("\trefs/tags/")[-1].strip())
    return tags


def _tag_info(repo_path: Path, tag: str) -> dict:
    """Return date, subject, tagger/committer for a tag."""
    # Try annotated tag first
    r = run_git(["cat-file", "-t", f"refs/tags/{tag}"], repo_path)
    is_annotated = r.stdout.strip() == "tag"

    if is_annotated:
        msg_r  = run_git(["tag", "-l", "--format=%(contents:subject)", tag], repo_path)
        date_r = run_git(["tag", "-l", "--format=%(creatordate:short)", tag], repo_path)
        tagger = run_git(["tag", "-l", "--format=%(taggername)", tag], repo_path)
        return {
            "annotated": True,
            "date": date_r.stdout.strip(),
            "subject": msg_r.stdout.strip(),
            "tagger": tagger.stdout.strip(),
        }
    else:
        date_r   = run_git(["log", "-1", "--format=%ci", tag], repo_path)
        commit_r = run_git(["log", "-1", "--format=%s", tag], repo_path)
        return {
            "annotated": False,
            "date": date_r.stdout.strip()[:10],
            "subject": commit_r.stdout.strip(),
            "tagger": "",
        }


def _pick_remote(repo_path: Path, default: str = "origin") -> Optional[str]:
    remotes = _get_remotes(repo_path)
    if not remotes:
        print(f"{Colors.RED}  No remotes configured.{Colors.RESET}")
        return None
    if len(remotes) == 1:
        print(f"  Using remote: {Colors.CYAN}{remotes[0]}{Colors.RESET}")
        return remotes[0]
    print(f"\n{Colors.BOLD}  Available remotes:{Colors.RESET}")
    for i, r in enumerate(remotes, 1):
        marker = f" {Colors.DIM}(default){Colors.RESET}" if r == default else ""
        print(f"    {i}. {r}{marker}")
    sel = safe_input(f"\n  {Colors.CYAN}Select remote (Enter={default}):{Colors.RESET} ").strip()
    if not sel:
        return default if default in remotes else remotes[0]
    if sel.isdigit():
        idx = int(sel) - 1
        return remotes[idx] if 0 <= idx < len(remotes) else remotes[0]
    return sel if sel in remotes else sel


def _pick_tags(tags: List[str], prompt: str, allow_all: bool = True) -> List[str]:
    """Let user pick one, several (space-separated), or all tags from a list."""
    if not tags:
        return []
    print()
    for i, t in enumerate(tags, 1):
        print(f"    {i:>3}. {t}")
    hint = "  number(s), name(s), glob e.g. 'CVE-*'"
    if allow_all:
        hint += ", or 'all'"
    sel = safe_input(f"\n  {Colors.CYAN}{prompt} [{hint}]:{Colors.RESET} ").strip()
    if not sel:
        return []
    if allow_all and sel.lower() == "all":
        return tags

    # Glob pattern?
    if "*" in sel or "?" in sel:
        import fnmatch
        matched = [t for t in tags if fnmatch.fnmatch(t, sel)]
        if not matched:
            print(f"  {Colors.YELLOW}No tags matched '{sel}'{Colors.RESET}")
        return matched

    # Numbers and/or names (space separated)
    chosen = []
    for token in sel.split():
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(tags):
                chosen.append(tags[idx])
        elif token in tags:
            chosen.append(token)
        else:
            # Try prefix match
            matches = [t for t in tags if t.startswith(token)]
            chosen.extend(matches)
    return list(dict.fromkeys(chosen))  # deduplicate, preserve order


# ── Operations ─────────────────────────────────────────────────────────────────

def op_list(repo_path: Path):
    """List all local tags with date and subject, and show which are on remote."""
    tags = _local_tags(repo_path)
    remotes = _get_remotes(repo_path)

    # Fetch remote tag sets quietly
    remote_tag_sets = {}
    for rem in remotes:
        remote_tag_sets[rem] = set(_remote_tags(repo_path, rem))

    if not tags:
        print(f"  {Colors.DIM}No local tags.{Colors.RESET}")
        safe_input("\n  Press Enter to continue...")
        return

    print(f"\n  {Colors.BOLD}{'TAG':<35} {'DATE':<12} {'TYPE':<10} SUBJECT{Colors.RESET}")
    print(f"  {'─'*35} {'─'*12} {'─'*10} {'─'*30}")

    for tag in tags:
        info = _tag_info(repo_path, tag)
        kind = f"{Colors.CYAN}annotated{Colors.RESET}" if info["annotated"] else f"{Colors.DIM}lightweight{Colors.RESET}"

        # Remote status indicators
        remote_marks = []
        for rem, rset in remote_tag_sets.items():
            if tag in rset:
                remote_marks.append(f"{Colors.GREEN}↑{rem}{Colors.RESET}")
            else:
                remote_marks.append(f"{Colors.RED}✗{rem}{Colors.RESET}")
        remote_str = " ".join(remote_marks) if remote_marks else ""

        subj = info["subject"][:45] + "…" if len(info["subject"]) > 45 else info["subject"]
        print(f"  {Colors.BOLD}{tag:<35}{Colors.RESET} {info['date']:<12} {kind:<20} {Colors.DIM}{subj}{Colors.RESET}  {remote_str}")

    print(f"\n  {len(tags)} local tag(s)")
    safe_input("\n  Press Enter to continue...")


def op_show(repo_path: Path):
    """Show full details for a specific tag."""
    tags = _local_tags(repo_path)
    if not tags:
        print(f"  {Colors.YELLOW}No local tags.{Colors.RESET}")
        safe_input("\n  Press Enter to continue...")
        return

    for i, t in enumerate(tags, 1):
        print(f"    {i:>3}. {t}")
    sel = safe_input(f"\n  {Colors.CYAN}Tag to inspect (number or name):{Colors.RESET} ").strip()
    tag = None
    if sel.isdigit():
        idx = int(sel) - 1
        tag = tags[idx] if 0 <= idx < len(tags) else None
    else:
        tag = sel if sel in tags else None
    if not tag:
        print(f"  {Colors.RED}Tag not found.{Colors.RESET}")
        return

    print(f"\n  {Colors.BOLD}{'='*55}{Colors.RESET}")
    print(f"  {Colors.BOLD}Tag: {Colors.CYAN}{tag}{Colors.RESET}")
    print(f"  {Colors.BOLD}{'='*55}{Colors.RESET}")
    # Full git show
    r = run_git(["show", "--stat", tag], repo_path)
    print(r.stdout[:3000])
    safe_input("\n  Press Enter to continue...")


def op_create(repo_path: Path):
    """Create a new tag (annotated by default)."""
    tag_name = safe_input(f"\n  {Colors.CYAN}Tag name (e.g. CVE-2026-21441-py38):{Colors.RESET} ").strip()
    if not tag_name:
        print(f"  {Colors.RED}Cancelled.{Colors.RESET}")
        return

    # Check if already exists
    existing = _local_tags(repo_path)
    if tag_name in existing:
        print(f"  {Colors.YELLOW}Tag '{tag_name}' already exists locally.{Colors.RESET}")
        overwrite = safe_input("  Overwrite? (y/n): ").strip().lower()
        if overwrite != "y":
            return
        run_git(["tag", "-d", tag_name], repo_path)

    ref = safe_input(f"  {Colors.CYAN}Commit/branch to tag (Enter=HEAD):{Colors.RESET} ").strip() or "HEAD"

    kind = safe_input(f"  {Colors.CYAN}Annotated tag with message? (Y/n):{Colors.RESET} ").strip().lower()
    if kind == "n":
        r = run_git(["tag", tag_name, ref], repo_path)
    else:
        msg = safe_input(f"  {Colors.CYAN}Tag message (Enter for tag name):{Colors.RESET} ").strip() or tag_name
        r = run_git(["tag", "-a", tag_name, ref, "-m", msg], repo_path)

    if r.returncode == 0:
        print(f"\n  {Colors.GREEN}✓ Created tag '{tag_name}'{Colors.RESET}")
        push = safe_input(f"  Push to remote now? (y/N): ").strip().lower()
        if push == "y":
            remote = _pick_remote(repo_path)
            if remote:
                _push_tags(repo_path, [tag_name], remote)
    else:
        print(f"  {Colors.RED}✗ Failed: {r.stderr.strip()}{Colors.RESET}")


def _push_tags(repo_path: Path, tags: List[str], remote: str):
    """Push a list of tags to a remote, reporting each result."""
    print(f"\n  {Colors.CYAN}Pushing {len(tags)} tag(s) to {remote}...{Colors.RESET}")
    ok = fail = 0
    for tag in tags:
        r = run_git(["push", remote, f"refs/tags/{tag}"], repo_path)
        if r.returncode == 0:
            print(f"    {Colors.GREEN}✓ {tag}{Colors.RESET}")
            ok += 1
        else:
            err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
            print(f"    {Colors.RED}✗ {tag}  —  {err}{Colors.RESET}")
            fail += 1
    print(f"\n  {Colors.GREEN}✓ {ok} pushed{Colors.RESET}" + (f"  {Colors.RED}✗ {fail} failed{Colors.RESET}" if fail else ""))


def op_push(repo_path: Path):
    """Push tags to a remote."""
    tags = _local_tags(repo_path)
    if not tags:
        print(f"  {Colors.YELLOW}No local tags to push.{Colors.RESET}")
        safe_input("\n  Press Enter to continue...")
        return

    remote = _pick_remote(repo_path)
    if not remote:
        return

    # Show which are already on remote
    remote_set = set(_remote_tags(repo_path, remote))
    unpushed = [t for t in tags if t not in remote_set]
    already  = [t for t in tags if t in remote_set]

    print(f"\n  {Colors.DIM}{len(already)} already on {remote}, {len(unpushed)} not yet pushed{Colors.RESET}")

    print(f"\n  {Colors.BOLD}What to push?{Colors.RESET}")
    print(f"    1. Only unpushed tags  ({len(unpushed)})")
    print(f"    2. Select specific tags")
    print(f"    3. Push ALL local tags  ({len(tags)})")
    print(f"    4. Back")

    choice = safe_input(f"\n  {Colors.CYAN}Choice (1-4):{Colors.RESET} ").strip()

    if choice == "1":
        if not unpushed:
            print(f"  {Colors.GREEN}All tags already on {remote}.{Colors.RESET}")
            return
        _push_tags(repo_path, unpushed, remote)
    elif choice == "2":
        picked = _pick_tags(tags, "Select tags to push")
        if picked:
            _push_tags(repo_path, picked, remote)
    elif choice == "3":
        confirm = safe_input(f"  Push all {len(tags)} tags to {remote}? (y/N): ").strip().lower()
        if confirm == "y":
            r = run_git(["push", remote, "--tags"], repo_path)
            if r.returncode == 0:
                print(f"  {Colors.GREEN}✓ All tags pushed to {remote}{Colors.RESET}")
            else:
                print(f"  {Colors.RED}✗ {r.stderr.strip()}{Colors.RESET}")
    else:
        return

    safe_input("\n  Press Enter to continue...")


def op_fetch(repo_path: Path):
    """Fetch tags from a remote."""
    remote = _pick_remote(repo_path)
    if not remote:
        return

    print(f"\n  {Colors.BOLD}Fetch options:{Colors.RESET}")
    print(f"    1. Fetch all tags from {remote}")
    print(f"    2. Fetch and prune (remove local tags deleted on remote)")
    print(f"    3. Back")

    choice = safe_input(f"\n  {Colors.CYAN}Choice (1-3):{Colors.RESET} ").strip()

    if choice == "1":
        print(f"  {Colors.CYAN}Fetching tags from {remote}...{Colors.RESET}")
        r = run_git(["fetch", remote, "--tags"], repo_path)
        if r.returncode == 0:
            print(f"  {Colors.GREEN}✓ Tags fetched from {remote}{Colors.RESET}")
            if r.stdout.strip():
                print(f"  {Colors.DIM}{r.stdout.strip()}{Colors.RESET}")
        else:
            print(f"  {Colors.RED}✗ {r.stderr.strip()}{Colors.RESET}")
    elif choice == "2":
        print(f"  {Colors.CYAN}Fetching and pruning tags from {remote}...{Colors.RESET}")
        r = run_git(["fetch", remote, "--tags", "--prune", "--prune-tags"], repo_path)
        if r.returncode == 0:
            print(f"  {Colors.GREEN}✓ Done{Colors.RESET}")
            if r.stdout.strip():
                print(f"  {Colors.DIM}{r.stdout.strip()}{Colors.RESET}")
        else:
            print(f"  {Colors.RED}✗ {r.stderr.strip()}{Colors.RESET}")
    else:
        return

    safe_input("\n  Press Enter to continue...")


def op_delete(repo_path: Path):
    """Delete tags locally, on remote, or both."""
    tags = _local_tags(repo_path)
    remotes = _get_remotes(repo_path)

    print(f"\n  {Colors.BOLD}Delete from:{Colors.RESET}")
    print(f"    1. Local only")
    print(f"    2. Remote only")
    print(f"    3. Both local and remote")
    print(f"    4. Back")

    where = safe_input(f"\n  {Colors.CYAN}Choice (1-4):{Colors.RESET} ").strip()
    if where == "4" or not where:
        return

    if where in ("1", "3"):
        if not tags:
            print(f"  {Colors.YELLOW}No local tags.{Colors.RESET}")
            if where == "1":
                return
        else:
            picked = _pick_tags(tags, "Select tags to delete locally")
            if not picked:
                print(f"  {Colors.DIM}Nothing selected.{Colors.RESET}")
            else:
                print(f"\n  {Colors.YELLOW}⚠  Will delete locally: {', '.join(picked)}{Colors.RESET}")
                confirm = safe_input("  Confirm? (y/N): ").strip().lower()
                if confirm == "y":
                    ok = fail = 0
                    for tag in picked:
                        r = run_git(["tag", "-d", tag], repo_path)
                        if r.returncode == 0:
                            print(f"    {Colors.GREEN}✓ deleted local: {tag}{Colors.RESET}")
                            ok += 1
                        else:
                            print(f"    {Colors.RED}✗ {tag}: {r.stderr.strip()}{Colors.RESET}")
                            fail += 1

    if where in ("2", "3"):
        remote = _pick_remote(repo_path)
        if not remote:
            safe_input("\n  Press Enter to continue...")
            return

        remote_tags = _remote_tags(repo_path, remote)
        if not remote_tags:
            print(f"  {Colors.YELLOW}No tags found on {remote}.{Colors.RESET}")
        else:
            # For "both", re-use same picks if they exist on remote too
            if where == "3" and "picked" in dir() and picked:
                on_remote = [t for t in picked if t in remote_tags]
                if not on_remote:
                    print(f"  {Colors.DIM}None of those tags exist on {remote} — skipping remote delete.{Colors.RESET}")
                    safe_input("\n  Press Enter to continue...")
                    return
                remote_picks = on_remote
            else:
                remote_picks = _pick_tags(remote_tags, f"Select tags to delete on {remote}")

            if remote_picks:
                print(f"\n  {Colors.RED}⚠  Will DELETE from {remote}: {', '.join(remote_picks)}{Colors.RESET}")
                confirm = safe_input("  Type 'yes' to confirm remote deletion: ").strip().lower()
                if confirm == "yes":
                    ok = fail = 0
                    for tag in remote_picks:
                        r = run_git(["push", remote, "--delete", f"refs/tags/{tag}"], repo_path)
                        if r.returncode == 0:
                            print(f"    {Colors.GREEN}✓ deleted remote: {tag}{Colors.RESET}")
                            ok += 1
                        else:
                            print(f"    {Colors.RED}✗ {tag}: {r.stderr.strip()}{Colors.RESET}")
                            fail += 1

    safe_input("\n  Press Enter to continue...")


def op_sync_status(repo_path: Path):
    """Compare local vs remote tags — show what's missing where."""
    remotes = _get_remotes(repo_path)
    if not remotes:
        print(f"  {Colors.YELLOW}No remotes configured.{Colors.RESET}")
        safe_input("\n  Press Enter to continue...")
        return

    local = set(_local_tags(repo_path))
    print(f"  {Colors.DIM}Checking remote(s)...{Colors.RESET}")

    for remote in remotes:
        remote_set = set(_remote_tags(repo_path, remote))
        only_local  = sorted(local - remote_set)
        only_remote = sorted(remote_set - local)
        in_both     = local & remote_set

        print(f"\n  {Colors.BOLD}── {remote} ──────────────────────────────{Colors.RESET}")
        print(f"  {Colors.GREEN}✓ In sync: {len(in_both)}{Colors.RESET}")
        if only_local:
            print(f"  {Colors.YELLOW}↑ Local only (not pushed): {len(only_local)}{Colors.RESET}")
            for t in only_local[:20]:
                print(f"      {t}")
            if len(only_local) > 20:
                print(f"      ... and {len(only_local)-20} more")
        if only_remote:
            print(f"  {Colors.CYAN}↓ Remote only (not fetched): {len(only_remote)}{Colors.RESET}")
            for t in only_remote[:20]:
                print(f"      {t}")
            if len(only_remote) > 20:
                print(f"      ... and {len(only_remote)-20} more")

        # Quick-action offer
        if only_local:
            push_now = safe_input(f"\n  Push {len(only_local)} unpushed tag(s) to {remote} now? (y/N): ").strip().lower()
            if push_now == "y":
                _push_tags(repo_path, only_local, remote)
        if only_remote:
            fetch_now = safe_input(f"  Fetch {len(only_remote)} remote-only tag(s) from {remote} now? (y/N): ").strip().lower()
            if fetch_now == "y":
                r = run_git(["fetch", remote, "--tags"], repo_path)
                if r.returncode == 0:
                    print(f"  {Colors.GREEN}✓ Fetched{Colors.RESET}")
                else:
                    print(f"  {Colors.RED}✗ {r.stderr.strip()}{Colors.RESET}")

    safe_input("\n  Press Enter to continue...")


# ── Menu ───────────────────────────────────────────────────────────────────────

def show_tag_menu(repo_path: Path):
    signal.signal(signal.SIGINT, _sigint_handler)
    try:
        _tag_menu_inner(repo_path)
    except UserCancelled:
        print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
        sys.exit(0)


def _tag_menu_inner(repo_path: Path):
    while True:
        local_tags = _local_tags(repo_path)
        remotes    = _get_remotes(repo_path)

        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}TAG MANAGEMENT{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"  Repository : {Colors.CYAN}{repo_path}{Colors.RESET}")
        print(f"  Local tags : {Colors.BRIGHT_GREEN}{len(local_tags)}{Colors.RESET}")
        print(f"  Remotes    : {Colors.BRIGHT_CYAN}{', '.join(remotes) if remotes else 'none'}{Colors.RESET}")

        # Peek at a few recent tags
        if local_tags:
            print(f"\n  {Colors.DIM}Recent: {', '.join(local_tags[:6])}" +
                  (f"  … +{len(local_tags)-6} more" if len(local_tags) > 6 else "") +
                  Colors.RESET)

        print(f"\n{Colors.BOLD}  Operations:{Colors.RESET}")
        print(f"    1. List all tags          (local + remote sync status)")
        print(f"    2. Show tag details        (commit, message, diff stat)")
        print(f"    3. Create tag              (annotated or lightweight)")
        print(f"    4. Push tags → remote")
        print(f"    5. Fetch tags ← remote")
        print(f"    6. Delete tags             (local / remote / both)")
        print(f"    7. Sync status             (what's missing where + quick fix)")
        print(f"    0. Back / Exit")

        try:
            choice = safe_input(f"\n{Colors.CYAN}Choose (0-7):{Colors.RESET} ").strip()
        except UserCancelled:
            break

        if   choice == "0": break
        elif choice == "1": op_list(repo_path)
        elif choice == "2": op_show(repo_path)
        elif choice == "3": op_create(repo_path)
        elif choice == "4": op_push(repo_path)
        elif choice == "5": op_fetch(repo_path)
        elif choice == "6": op_delete(repo_path)
        elif choice == "7": op_sync_status(repo_path)
        else:
            print(f"  {Colors.RED}Invalid choice.{Colors.RESET}")


# ── Public entry points (matches gitship convention) ─────────────────────────

def main_with_repo(repo_path: Path):
    show_tag_menu(repo_path)


def main_with_args(repo_path: Path, operation: str = None, **kwargs):
    """CLI entry point for non-interactive use."""
    if not operation:
        show_tag_menu(repo_path)
        return

    op = operation.lower()
    remote = kwargs.get("remote", "origin")

    if op == "list":
        op_list(repo_path)
    elif op == "push":
        tags = kwargs.get("tags") or []
        if tags:
            _push_tags(repo_path, tags, remote)
        elif kwargs.get("all"):
            r = run_git(["push", remote, "--tags"], repo_path)
            print(f"{'✓' if r.returncode == 0 else '✗'} {r.stdout.strip() or r.stderr.strip()}")
        else:
            op_push(repo_path)
    elif op == "fetch":
        r = run_git(["fetch", remote, "--tags"], repo_path)
        print(f"{'✓' if r.returncode == 0 else '✗'} {r.stdout.strip() or r.stderr.strip()}")
    elif op == "status":
        op_sync_status(repo_path)
    elif op == "create":
        op_create(repo_path)
    elif op == "delete":
        op_delete(repo_path)
    else:
        show_tag_menu(repo_path)


def main():
    import argparse
    p = argparse.ArgumentParser(description="gitship tag manager")
    p.add_argument("operation", nargs="?",
                   choices=["list", "push", "fetch", "status", "create", "delete"],
                   help="Operation to perform (interactive menu if omitted)")
    p.add_argument("--remote", default="origin")
    p.add_argument("--all", action="store_true", dest="push_all",
                   help="Push/fetch all tags")
    p.add_argument("tags", nargs="*", help="Specific tag names")
    args = p.parse_args()
    main_with_args(
        Path.cwd(),
        operation=args.operation,
        remote=args.remote,
        all=args.push_all,
        tags=args.tags,
    )


if __name__ == "__main__":
    main()