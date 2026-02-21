"""
gitship init - Initialize a new git repository with sane defaults.

Handles the common case where a project folder exists but has no .git,
or where the .git directory got corrupted. Walks the user through:
  1. Stash working tree to a safe location before touching anything
  2. Run git fsck to assess corruption severity
  3. Attempt git gc recovery (non-destructive)
  4. If commit fails with invalid-object errors, offer VSCode history restore
  5. Optionally nuke .git and start fresh (with history rescue attempt first)
  6. First commit
  7. Optional: create GitHub repo and push (via gitship publish)
"""

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# Standard Python .gitignore template
PYTHON_GITIGNORE = """\
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# Distribution / packaging
.eggs/
dist/
build/
*.egg-info/
*.egg
.installed.cfg

# Virtual environments
.env
.venv
env/
venv/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo

# Testing
.pytest_cache/
.coverage
htmlcov/

# Misc
*.log
*.bak
*.backup
*.backup2
.DS_Store
Thumbs.db
"""


# ── Shell helpers ───────────────────────────────────────────────────────────────

def _run(cmd: list, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=capture, text=True)


def _git(args: list, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess:
    return _run(["git"] + args, cwd=cwd, capture=capture)


# ── Repo state detection ────────────────────────────────────────────────────────

def is_git_repo(path: Path) -> bool:
    return _git(["rev-parse", "--git-dir"], path, capture=True).returncode == 0


def is_corrupted(path: Path) -> bool:
    """True if .git exists but git status fails."""
    if not (path / ".git").exists():
        return False
    return _git(["status"], path, capture=True).returncode != 0


def _fsck_summary(path: Path) -> tuple[bool, list[str]]:
    """
    Run git fsck --full and return (has_errors, error_lines).
    """
    result = _git(["fsck", "--full"], path, capture=True)
    errors = [
        line for line in (result.stdout + result.stderr).splitlines()
        if any(kw in line for kw in ["error", "missing", "corrupt", "dangling"])
    ]
    return bool(errors), errors


def _try_gc_recovery(path: Path) -> bool:
    """
    Attempt git gc --aggressive as a non-destructive recovery step.
    Returns True if git status passes afterward.
    """
    print("  Running git gc --aggressive (non-destructive repair)...")
    _git(["gc", "--aggressive", "--prune=now"], path, capture=True)
    return _git(["status"], path, capture=True).returncode == 0


# ── Working tree stash ─────────────────────────────────────────────────────────

def _stash_working_tree(repo_path: Path) -> Path:
    """
    Copy the working tree (excluding .git) to a timestamped safety directory
    under ~/.gitship/stash/.  Returns the stash path.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_root = Path.home() / ".gitship" / "stash"
    stash_dest = stash_root / f"{repo_path.name}_{stamp}"
    stash_dest.mkdir(parents=True, exist_ok=True)

    print(f"  Stashing working tree → {stash_dest}")

    def _ignore(src, names):
        return {".git"} & set(names)

    shutil.copytree(str(repo_path), str(stash_dest), ignore=_ignore, dirs_exist_ok=True)
    print(f"  ✓ Working tree saved to: {stash_dest}")
    return stash_dest


# ── .gitignore ─────────────────────────────────────────────────────────────────

def write_gitignore(repo_path: Path) -> bool:
    gi_path = repo_path / ".gitignore"
    if gi_path.exists():
        overwrite = input(
            "\n  .gitignore already exists. Overwrite with Python template? [y/N]: "
        ).strip().lower()
        if overwrite != "y":
            print("  → Keeping existing .gitignore")
            return False

    gi_path.write_text(PYTHON_GITIGNORE, encoding="utf-8")
    print("  ✓ Written .gitignore (Python template)")
    return True


# ── User identity ──────────────────────────────────────────────────────────────

def configure_user(repo_path: Path):
    name = _git(["config", "--global", "user.name"],  repo_path, capture=True).stdout.strip()
    email = _git(["config", "--global", "user.email"], repo_path, capture=True).stdout.strip()

    if name and email:
        print(f"  ✓ Git identity: {name} <{email}>")
        return

    print("\n  ⚠️  Git user identity not configured.")
    if not name:
        name = input("  Your name: ").strip()
        if name:
            _git(["config", "--global", "user.name", name], repo_path)
    if not email:
        email = input("  Your email: ").strip()
        if email:
            _git(["config", "--global", "user.email", email], repo_path)
    print("  ✓ Git identity saved globally")


# ── Blob healing ───────────────────────────────────────────────────────────────

EMPTY_BLOB_SHA = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def _parse_invalid_object_paths(stderr: str, repo_path: Path) -> list[tuple[Path, str]]:
    """
    Extract (absolute_path, sha) pairs from lines like:
      error: invalid object 100644 <sha> for '<rel_path>'
    Deduplicates by path.
    """
    seen: set[Path] = set()
    results = []
    pattern = re.compile(r"error: invalid object \S+ (\S+) for '(.+?)'")
    for match in pattern.finditer(stderr):
        sha, rel = match.group(1), match.group(2)
        p = (repo_path / rel).resolve()
        if p not in seen:
            seen.add(p)
            results.append((p, sha))
    return results


def _heal_invalid_blobs(repo_path: Path, bad_entries: list[tuple[Path, str]]) -> int:
    """
    For each file whose blob git can't find, figure out what to do:

    Case A — empty blob (SHA e69de29…):
        The file is supposed to be empty. If it exists on disk and is already
        empty, git just lost the object. Fix: unstage → touch → re-stage so
        git writes a fresh object from scratch.
        If the file doesn't exist on disk, create it empty.

    Case B — non-empty blob, file exists on disk:
        Git lost the stored blob but we still have the real content on disk.
        Fix: unstage → re-stage (git will re-hash from disk content).

    Case C — non-empty blob, file missing from disk entirely:
        Real data loss. Report it, offer VSCode history recovery if available.

    Returns number of files successfully healed.
    """
    healed = 0

    for abs_path, sha in bad_entries:
        try:
            rel = str(abs_path.relative_to(repo_path))
        except ValueError:
            rel = str(abs_path)

        on_disk = abs_path.exists()
        disk_size = abs_path.stat().st_size if on_disk else -1
        is_empty_blob = (sha == EMPTY_BLOB_SHA)

        print(f"\n  🔧 Healing: {rel}")

        if is_empty_blob:
            # The file should be empty — create or recreate it cleanly
            if on_disk and disk_size > 0:
                print(f"     ⚠️  File on disk has content ({disk_size}B) but git staged "
                      f"it as empty (empty blob SHA). Re-staging from disk content.")
            else:
                if not on_disk:
                    print("     File missing from disk — creating empty file.")
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    print("     File is empty on disk — refreshing git object.")
                # Write/rewrite as empty
                abs_path.write_bytes(b"")

            # Unstage then re-stage so git writes a clean object
            _git(["rm", "--cached", rel], repo_path, capture=True)
            _git(["add", rel], repo_path, capture=True)
            healed += 1

        elif on_disk and disk_size >= 0:
            # Non-empty blob but file exists on disk — re-hash from disk
            print(f"     File exists on disk ({disk_size}B) — re-staging from disk content.")
            _git(["rm", "--cached", rel], repo_path, capture=True)
            _git(["add", rel], repo_path, capture=True)
            healed += 1

        else:
            # File is gone and blob is gone — real data loss
            print(f"     ✗ File missing from disk and blob is lost.")

            # Try VSCode history
            try:
                from gitship.vscode_history import offer_restore_for_missing
            except ImportError:
                try:
                    from vscode_history import offer_restore_for_missing
                except ImportError:
                    offer_restore_for_missing = None

            if offer_restore_for_missing:
                restored = offer_restore_for_missing(repo_path, [abs_path])
                if restored:
                    _git(["add", rel], repo_path, capture=True)
                    healed += 1
                    continue

            # Last resort: offer to create a placeholder empty file
            print(f"     No VSCode history found.")
            create = input(f"     Create as empty placeholder and continue? [Y/n]: ").strip().lower()
            if create != "n":
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(b"")
                _git(["rm", "--cached", rel], repo_path, capture=True)
                _git(["add", rel], repo_path, capture=True)
                print(f"     ✓ Created empty placeholder: {rel}")
                healed += 1
            else:
                # Remove from index entirely so commit can proceed without it
                _git(["rm", "--cached", rel], repo_path, capture=True)
                print(f"     ↷ Removed from index — file will not be in first commit.")

    return healed


# ── Commit ──────────────────────────────────────────────────────────────────────

def make_first_commit(repo_path: Path) -> bool:
    """
    Stage everything and make the initial commit.

    If the commit fails due to invalid-object errors, automatically heal
    the broken blobs (re-stage from disk, fix empty files, or offer VSCode
    history recovery) and retry once.
    """
    result = _git(["status", "--porcelain"], repo_path, capture=True)
    if not result.stdout.strip():
        print("  ℹ️  Nothing to commit — working tree is clean")
        return True

    print("\n  Staging all files...")
    _git(["add", "."], repo_path)

    msg = input('  Commit message [initial commit]: ').strip() or "initial commit"

    # First attempt
    result = _git(["commit", "-m", msg], repo_path, capture=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode == 0:
        print(f"  ✓ Committed: {msg}")
        return True

    # Parse which blobs are broken
    bad_entries = _parse_invalid_object_paths(result.stderr, repo_path)

    if bad_entries:
        print()
        print(f"  ✗ Commit failed — {len(bad_entries)} file(s) have broken blob objects.")
        print("  Auto-healing before retry...\n")
        healed = _heal_invalid_blobs(repo_path, bad_entries)
        print(f"\n  Healed {healed}/{len(bad_entries)} file(s). Retrying commit...")

        result2 = _git(["commit", "-m", msg], repo_path, capture=True)
        if result2.stdout:
            print(result2.stdout, end="")
        if result2.returncode == 0:
            print(f"  ✓ Committed: {msg}")
            return True
        else:
            print("  ✗ Commit still failed after healing.")
            if result2.stderr:
                print(result2.stderr)
    else:
        # Non-blob failure — show raw error
        print("  ✗ Commit failed.")
        if result.stderr:
            print(result.stderr)

    print()
    print("  Options:")
    print("    [r]  Reset index and try again from scratch")
    print("    [s]  Skip commit (repo will have no commits — you can commit manually later)")
    print("    [q]  Quit")
    sub = input("  Choice [s]: ").strip().lower() or "s"

    if sub == "r":
        print("  Resetting index...")
        _git(["rm", "-r", "--cached", "."], repo_path, capture=True)
        _git(["add", "."], repo_path)
        result3 = _git(["commit", "-m", msg], repo_path)
        return result3.returncode == 0

    return False


# ── Clone rescue (before nuke) ─────────────────────────────────────────────────

def _attempt_rescue_clone(repo_path: Path) -> Path | None:
    """
    Try to clone readable objects into a sibling rescue directory.
    Returns the rescue path on success, None if clone failed entirely.
    """
    rescue_path = repo_path.parent / f"{repo_path.name}_rescued"
    print(f"\n  Attempting to rescue readable history → {rescue_path}")
    result = _run(
        ["git", "clone", "--local", "--no-hardlinks", str(repo_path), str(rescue_path)],
        cwd=repo_path.parent,
        capture=True,
    )
    if result.returncode == 0:
        print("  ✓ Rescue clone succeeded — readable history preserved")
        return rescue_path
    else:
        print("  ⚠️  Rescue clone failed (object store too damaged)")
        if rescue_path.exists():
            shutil.rmtree(rescue_path, ignore_errors=True)
        return None


# ── Publish offer ──────────────────────────────────────────────────────────────

def _safe_push(repo_path: Path):
    """
    Push to remote with full safety checks:
      1. Stash any unstaged changes
      2. Fetch remote
      3. Detect divergence → rebase if behind, fast-forward if ahead only
      4. Push
      5. Restore stash
    """
    branch_result = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path, capture=True)
    branch = branch_result.stdout.strip()

    # ── Step 1: stash unstaged changes ────────────────────────────────────────
    dirty = _git(["status", "--porcelain"], repo_path, capture=True).stdout.strip()
    stashed = False
    if dirty:
        print("  ⚠️  Unstaged changes detected — stashing before push...")
        stash_result = _git(["stash", "push", "-m", "gitship-init-autopush"], repo_path, capture=True)
        stashed = "No local changes" not in stash_result.stdout
        if stashed:
            print("  ✓ Changes stashed")

    try:
        # ── Step 2: fetch ──────────────────────────────────────────────────────
        print("  Fetching remote...")
        fetch = _git(["fetch", "origin"], repo_path, capture=True)
        if fetch.returncode != 0:
            print(f"  ⚠️  Fetch failed: {fetch.stderr.strip()}")
            return

        # ── Step 3: check divergence ───────────────────────────────────────────
        remote_ref = f"origin/{branch}"

        # Check if remote branch exists at all
        remote_exists = _git(
            ["ls-remote", "--exit-code", "--heads", "origin", branch],
            repo_path, capture=True
        ).returncode == 0

        if remote_exists:
            behind = _git(
                ["rev-list", "--count", f"HEAD..{remote_ref}"],
                repo_path, capture=True
            ).stdout.strip()
            ahead = _git(
                ["rev-list", "--count", f"{remote_ref}..HEAD"],
                repo_path, capture=True
            ).stdout.strip()

            behind, ahead = int(behind or 0), int(ahead or 0)

            if behind > 0:
                print(f"  ℹ️  Local is {behind} commit(s) behind remote, {ahead} ahead — rebasing...")
                rebase = _git(["pull", "--rebase", "origin", branch], repo_path, capture=True)
                if rebase.returncode != 0:
                    print("  ✗ Rebase failed. Resolve conflicts manually then push.")
                    print(rebase.stdout)
                    print(rebase.stderr)
                    return
                print("  ✓ Rebase complete")
            elif ahead == 0:
                print("  ✓ Already up to date with remote — nothing to push")
                return
            else:
                print(f"  ✓ Local is {ahead} commit(s) ahead — pushing...")
        else:
            print(f"  ℹ️  Remote branch '{branch}' doesn't exist yet — pushing as new branch...")

        # ── Step 4: push ───────────────────────────────────────────────────────
        push = _git(["push", "-u", "origin", f"HEAD:{branch}"], repo_path, capture=True)
        if push.returncode == 0:
            print(f"  ✓ Pushed to origin/{branch}")
        else:
            print(f"  ✗ Push failed: {push.stderr.strip()}")

    finally:
        # ── Step 5: restore stash ──────────────────────────────────────────────
        if stashed:
            print("  Restoring stashed changes...")
            pop = _git(["stash", "pop"], repo_path, capture=True)
            if pop.returncode == 0:
                print("  ✓ Stash restored")
            else:
                print("  ⚠️  Stash pop had conflicts — run 'git stash pop' manually")


def _offer_publish(repo_path: Path):
    result = _run(["git", "remote", "get-url", "origin"], repo_path, capture=True)
    if result.returncode == 0:
        print(f"\n  ✓ Remote already set: {result.stdout.strip()}")
        push = input("  Push to remote now? [Y/n]: ").strip().lower()
        if push != "n":
            _safe_push(repo_path)
        return

    push = input(
        "\n  No remote configured. Create GitHub repo and push? [Y/n]: "
    ).strip().lower()
    if push == "n":
        print("\n  Done. To push later:  gitship publish")
        return

    try:
        from gitship import publish
        publish.main_with_repo(repo_path)
    except ImportError:
        print("\n  ℹ️  Run 'gitship publish' to create a GitHub repo and push.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main_with_repo(repo_path: Path):
    print("\n" + "=" * 60)
    print("GITSHIP INIT")
    print("=" * 60)
    print(f"  Directory: {repo_path}")

    git_dir = repo_path / ".git"

    # ── Already healthy ────────────────────────────────────────────────────────
    if is_git_repo(repo_path) and not is_corrupted(repo_path):
        print("\n  ✓ Already a valid git repository.")
        result = _git(["log", "--oneline", "-1"], repo_path, capture=True)
        if result.stdout.strip():
            print(f"  Latest commit: {result.stdout.strip()}")
        else:
            print("  No commits yet.")
            make_first_commit(repo_path)
        _offer_publish(repo_path)
        return

    # ── Corrupted .git ─────────────────────────────────────────────────────────
    if git_dir.exists() and is_corrupted(repo_path):
        print("\n  ⚠️  Detected corrupted .git directory.")

        # Step 1: stash working tree immediately (before we touch anything)
        stash_path = _stash_working_tree(repo_path)

        # Step 2: assess with fsck
        has_errors, fsck_errors = _fsck_summary(repo_path)
        if has_errors:
            print(f"\n  git fsck found {len(fsck_errors)} issue(s):")
            for line in fsck_errors[:8]:
                print(f"    {line}")
            if len(fsck_errors) > 8:
                print(f"    ... ({len(fsck_errors) - 8} more)")

        # Step 3: try non-destructive gc recovery first
        print()
        recovered = _try_gc_recovery(repo_path)
        if recovered:
            print("  ✓ Repository recovered via git gc!")
            make_first_commit(repo_path)
            _offer_publish(repo_path)
            return

        # Step 4: gc didn't fix it — present options
        print("\n  gc recovery did not fix the repository.")
        print("\n  Options:")
        print("    1. Reinitialize in-place (keep what survived in .git)")
        print("    2. Rescue readable history → sibling dir, then start fresh")
        print("    3. Nuke .git and start completely fresh (lose all history)")
        print("    0. Abort  (your working tree is stashed safely)")
        print(f"\n  Note: Working tree already stashed → {stash_path}")
        choice = input("\n  Choice [2]: ").strip() or "2"

        if choice == "0":
            print(f"  Aborted. Your working tree stash is at:\n    {stash_path}")
            return

        elif choice == "2":
            rescued = _attempt_rescue_clone(repo_path)
            if rescued:
                print(f"\n  Rescued history is at: {rescued}")
                print("  You can inspect it later with: cd {rescued} && git log")
            # Fall through to fresh init

            confirm = input(
                f"\n  ⚠️  Will now remove {git_dir} and start fresh.\n"
                "  Type YES to confirm: "
            ).strip()
            if confirm != "YES":
                print("  Aborted.")
                return
            shutil.rmtree(git_dir)
            print("  ✓ Removed corrupted .git")

        elif choice == "3":
            confirm = input(
                f"\n  ⚠️  This will permanently delete {git_dir} (ALL history lost).\n"
                "  Type YES to confirm: "
            ).strip()
            if confirm != "YES":
                print("  Aborted.")
                return
            shutil.rmtree(git_dir)
            print("  ✓ Removed corrupted .git")

        # choice == "1" falls through directly to git init below

    # ── Fresh init (no .git, or just nuked) ───────────────────────────────────
    print("\n  Running git init...")
    result = _git(["init"], repo_path)
    if result.returncode != 0:
        print("  ✗ git init failed")
        sys.exit(1)

    # Suppress the "defaultBranch" hint noise
    _git(["config", "init.defaultBranch", "main"], repo_path)
    print("  ✓ Initialized empty repository (branch: main)")

    configure_user(repo_path)
    write_gitignore(repo_path)
    make_first_commit(repo_path)
    _offer_publish(repo_path)