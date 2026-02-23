#!/usr/bin/env python3
"""
stash - Stash management for gitship.

Provides full stash visibility and control:
- List stashes with actual file contents (not just WIP messages)
- Apply/pop/drop individual stashes
- Apply only specific files from a stash
- Handle .po/.mo conflicts atomically
"""

import subprocess
from pathlib import Path
from typing import List, Dict, Optional


class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    CYAN = '\033[36m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_BLUE = '\033[94m'


def safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print()
        raise SystemExit(0)


def run_git(args: List[str], repo_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        encoding='utf-8',
        errors='replace'
    )


def get_stash_list(repo_path: Path) -> List[Dict]:
    """Get all stashes with their file lists."""
    result = run_git(["stash", "list", "--format=%gd|||%s|||%cr"], repo_path)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    stashes = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|||", 2)
        if len(parts) < 2:
            continue
        ref = parts[0].strip()       # e.g. stash@{0}
        message = parts[1].strip()   # e.g. On main: Auto-stash...
        age = parts[2].strip() if len(parts) > 2 else ""

        # Get actual files in this stash
        files_result = run_git(["stash", "show", "--name-status", ref], repo_path)
        files = []
        if files_result.returncode == 0:
            for fline in files_result.stdout.strip().splitlines():
                fline = fline.strip()
                if fline:
                    fparts = fline.split(None, 1)
                    if len(fparts) == 2:
                        files.append({"status": fparts[0], "path": fparts[1]})

        # Get the branch it was stashed on
        branch = "unknown"
        if message.startswith("On "):
            branch = message.split(":", 1)[0].replace("On ", "").strip()
        elif message.startswith("WIP on "):
            branch = message.split(":", 1)[0].replace("WIP on ", "").strip()

        stashes.append({
            "ref": ref,
            "message": message,
            "age": age,
            "branch": branch,
            "files": files,
        })

    return stashes


def show_stash_list(stashes: List[Dict]):
    """Print stash list with file contents."""
    if not stashes:
        print(f"\n{Colors.YELLOW}No stashes found.{Colors.RESET}")
        return

    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}STASH LIST ({len(stashes)} entries){Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}")

    for i, s in enumerate(stashes):
        ref_label = f"{Colors.CYAN}{s['ref']}{Colors.RESET}"
        age_label = f"{Colors.DIM}{s['age']}{Colors.RESET}"
        branch_label = f"{Colors.BRIGHT_GREEN}{s['branch']}{Colors.RESET}"
        print(f"\n  {i}. {ref_label}  [{branch_label}]  {age_label}")
        print(f"     {Colors.DIM}{s['message']}{Colors.RESET}")
        if s['files']:
            for f in s['files']:
                status_color = Colors.GREEN if f['status'] == 'A' else Colors.YELLOW if f['status'] == 'M' else Colors.RED
                print(f"       {status_color}{f['status']}{Colors.RESET}  {f['path']}")
        else:
            print(f"       {Colors.DIM}(no files){Colors.RESET}")


def apply_stash_smart(repo_path: Path, ref: str, files_only: Optional[List[str]] = None):
    """
    Apply a stash, handling .po/.mo conflicts atomically.
    
    If files_only is given, only restore those specific files from the stash.
    Otherwise applies the whole stash, committing any blocking dirty files first.
    """
    # Check for blocking dirty files
    status = run_git(["status", "--porcelain"], repo_path)
    dirty = [l[3:].strip() for l in status.stdout.strip().splitlines() if l.strip()]

    if dirty:
        # Find which dirty files would conflict with the stash
        stash_files_result = run_git(["stash", "show", "--name-only", ref], repo_path)
        stash_files = set(stash_files_result.stdout.strip().splitlines()) if stash_files_result.returncode == 0 else set()
        conflicts = [f for f in dirty if f in stash_files]

        if conflicts:
            print(f"\n{Colors.YELLOW}⚠  These files are dirty and would conflict with the stash:{Colors.RESET}")
            for f in conflicts:
                print(f"   • {f}")
            print(f"\nWhat do you want to do with them?")
            print(f"  1. Commit them first, then apply stash")
            print(f"  2. Discard them (keep stash version)")
            print(f"  3. Cancel")
            choice = safe_input(f"\n{Colors.CYAN}Choice (1-3):{Colors.RESET} ").strip()

            if choice == "1":
                run_git(["add"] + conflicts, repo_path)
                msg = safe_input(f"{Colors.CYAN}Commit message:{Colors.RESET} ").strip() or "wip: save before applying stash"
                res = run_git(["commit", "-m", msg], repo_path)
                if res.returncode != 0:
                    print(f"{Colors.RED}✗ Commit failed: {res.stderr.strip()}{Colors.RESET}")
                    return False
                print(f"{Colors.GREEN}✓ Committed{Colors.RESET}")
            elif choice == "2":
                run_git(["checkout", "--"] + conflicts, repo_path)
                print(f"{Colors.GREEN}✓ Discarded dirty versions{Colors.RESET}")
            else:
                print("Cancelled.")
                return False

    if files_only:
        # Restore only specific files from the stash using checkout
        print(f"\n{Colors.CYAN}Restoring {len(files_only)} file(s) from {ref}...{Colors.RESET}")
        success = []
        failed = []
        for f in files_only:
            res = run_git(["checkout", ref, "--", f], repo_path)
            if res.returncode == 0:
                success.append(f)
            else:
                failed.append((f, res.stderr.strip()))
        if success:
            print(f"{Colors.GREEN}✓ Restored: {', '.join(success)}{Colors.RESET}")
        if failed:
            for f, err in failed:
                print(f"{Colors.RED}✗ {f}: {err}{Colors.RESET}")
        return len(success) > 0
    else:
        res = run_git(["stash", "apply", ref], repo_path)
        if res.returncode == 0:
            print(f"{Colors.GREEN}✓ Stash applied{Colors.RESET}")
            return True
        else:
            print(f"{Colors.RED}✗ Apply failed: {res.stderr.strip()}{Colors.RESET}")
            return False


def run_stash_menu(repo_path: Path):
    """Interactive stash manager."""
    while True:
        stashes = get_stash_list(repo_path)
        show_stash_list(stashes)

        print(f"\n{Colors.BOLD}Actions:{Colors.RESET}")
        print("  a  <n>  — Apply stash n (smart: handles conflicts)")
        print("  f  <n>  — Apply specific files from stash n")
        print("  p  <n>  — Pop stash n (apply + drop)")
        print("  d  <n>  — Drop stash n")
        print("  D       — Drop ALL stashes (dangerous)")
        print("  0       — Back")

        raw = safe_input(f"\n{Colors.BRIGHT_BLUE}Action:{Colors.RESET} ").strip()
        if not raw or raw == "0":
            break

        parts = raw.split()
        cmd = parts[0].lower()
        idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

        if cmd in ("a", "p", "f") and idx is None:
            print(f"{Colors.RED}Specify stash number, e.g. 'a 0'{Colors.RESET}")
            continue

        if cmd in ("a", "p", "f") and (idx < 0 or idx >= len(stashes)):
            print(f"{Colors.RED}Invalid stash index{Colors.RESET}")
            continue

        if cmd == "a":
            apply_stash_smart(repo_path, stashes[idx]["ref"])

        elif cmd == "f":
            s = stashes[idx]
            if not s["files"]:
                print(f"{Colors.YELLOW}No files in this stash.{Colors.RESET}")
                continue
            print(f"\nFiles in {s['ref']}:")
            for i, f in enumerate(s["files"]):
                print(f"  {i}. {f['status']}  {f['path']}")
            sel = safe_input(f"\n{Colors.CYAN}File numbers to restore (comma-separated, or 'all'):{Colors.RESET} ").strip()
            if sel.lower() == "all":
                chosen = [f["path"] for f in s["files"]]
            else:
                indices = [int(x.strip()) for x in sel.split(",") if x.strip().isdigit()]
                chosen = [s["files"][i]["path"] for i in indices if 0 <= i < len(s["files"])]
            if chosen:
                apply_stash_smart(repo_path, s["ref"], files_only=chosen)

        elif cmd == "p":
            if apply_stash_smart(repo_path, stashes[idx]["ref"]):
                run_git(["stash", "drop", stashes[idx]["ref"]], repo_path)
                print(f"{Colors.GREEN}✓ Stash dropped{Colors.RESET}")

        elif cmd == "d":
            confirm = safe_input(f"{Colors.YELLOW}Drop {stashes[idx]['ref']}? (y/n):{Colors.RESET} ").strip().lower()
            if confirm == "y":
                run_git(["stash", "drop", stashes[idx]["ref"]], repo_path)
                print(f"{Colors.GREEN}✓ Dropped{Colors.RESET}")

        elif cmd == "D":
            confirm = safe_input(f"{Colors.RED}Drop ALL {len(stashes)} stashes? Type 'yes':{Colors.RESET} ").strip()
            if confirm == "yes":
                run_git(["stash", "clear"], repo_path)
                print(f"{Colors.GREEN}✓ All stashes cleared{Colors.RESET}")
                break


def main():
    """Standalone CLI entry point: gitship stash"""
    import sys
    repo_path = Path.cwd()
    run_stash_menu(repo_path)


if __name__ == "__main__":
    main()