#!/usr/bin/env python3
"""
Merge Conflict Resolver - Interactive conflict resolution helper
Shows conflicts, offers ours/theirs/manual choices.
After resolving all conflicts, automatically offers to continue/complete
the rebase/merge/cherry-pick so you never get stuck mid-operation.
"""

import os
import sys
import subprocess
import re
from pathlib import Path
from typing import List, Tuple


def run_git(args, check=True, cwd=None):
    """Run git command and return stdout."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=check,
            cwd=cwd,
            encoding='utf-8',
            errors='replace'
        )
        return res
    except subprocess.CalledProcessError as e:
        if not check:
            return e
        print(f"Git error: {e.stderr}")
        return e


def run_git_str(args, check=True, cwd=None) -> str:
    """Run git command and return stdout as a string (legacy helper)."""
    res = run_git(args, check=check, cwd=cwd)
    if hasattr(res, 'stdout'):
        return res.stdout.strip()
    return ""


def get_conflicted_files() -> List[str]:
    """Get list of files with merge conflicts."""
    output = run_git_str(["diff", "--name-only", "--diff-filter=U"])
    return [f for f in output.split('\n') if f]


def parse_conflict_blocks(content: str) -> List[dict]:
    """Parse conflict markers and extract blocks."""
    blocks = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        if lines[i].startswith('<<<<<<<'):
            conflict_start = i
            ours_label = lines[i].replace('<<<<<<<', '').strip()

            middle = i + 1
            while middle < len(lines) and not lines[middle].startswith('======='):
                middle += 1

            end = middle + 1
            while end < len(lines) and not lines[end].startswith('>>>>>>>'):
                end += 1

            if middle < len(lines) and end < len(lines):
                theirs_label = lines[end].replace('>>>>>>>', '').strip()

                blocks.append({
                    'start': conflict_start,
                    'end': end,
                    'ours_label': ours_label,
                    'theirs_label': theirs_label,
                    'ours': '\n'.join(lines[conflict_start + 1:middle]),
                    'theirs': '\n'.join(lines[middle + 1:end])
                })
                i = end + 1
            else:
                i += 1
        else:
            i += 1

    return blocks


def show_conflict(filepath: str, block_num: int, block: dict, total: int):
    """Display a single conflict block."""
    print("\n" + "=" * 80)
    print(f"📁 File: {filepath}")
    print(f"🔀 Conflict {block_num}/{total}")
    print("=" * 80)

    print(f"\n🔵 OURS (LOCAL - {block['ours_label']}):")
    print("─" * 80)
    print(block['ours'] if block['ours'] else "(empty)")

    print(f"\n🔴 THEIRS (REMOTE - {block['theirs_label']}):")
    print("─" * 80)
    print(block['theirs'] if block['theirs'] else "(empty)")

    print("\n" + "=" * 80)


def resolve_conflict_interactive(filepath: str, ours_label: str = 'OURS (local)', theirs_label: str = 'THEIRS (incoming)') -> bool:
    """Interactively resolve conflicts in a file."""
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return False

    content = path.read_text(encoding='utf-8', errors='replace')
    blocks = parse_conflict_blocks(content)

    if not blocks:
        print(f"No conflicts found in {filepath}")
        return False

    print(f"\n📝 Found {len(blocks)} conflict(s) in {filepath}")
    print(f"  🔵 O = OURS   → keep '{ours_label}'")
    print(f"  🔴 T = THEIRS → keep '{theirs_label}'")
    print("\nHow do you want to resolve this file?")
    print("  V - VIEW full diff first")
    print("  F - Save full diff to FILE")
    print(f"  O - Keep ALL blocks as OURS   ('{ours_label}')")
    print(f"  T - Keep ALL blocks as THEIRS ('{theirs_label}')")
    print("  B - Resolve BLOCK-BY-BLOCK (choose per conflict)")
    print("  S - Skip this file")
    print("  Q - Quit resolver")

    choice = input("\nChoice (V/F/O/T/B/S/Q): ").strip().upper()

    if choice == 'V':
        diff_output = run_git_str(["diff", filepath], check=False)
        lines = diff_output.split('\n')
        line_count = len(lines)

        print(f"\n📊 Diff has {line_count} lines")
        preview_lines = min(50, line_count)
        print(f"\n📋 Preview (first {preview_lines} lines):")
        print("─" * 80)
        print('\n'.join(lines[:preview_lines]))
        if line_count > preview_lines:
            print(f"\n... ({line_count - preview_lines} more lines)")
        print("─" * 80)

        if line_count > 100:
            print("\n🔍 Full diff viewing options:")
            print("  1. less - Pager (searchable, press q to quit)")
            print("  2. cat  - Print all to terminal")
            print("  3. nano - Text editor")
            print("  4. vim  - Vim editor (if you like pain)")
            print("  5. Save to file and skip viewing")
            print("  6. Continue with just the preview")
            view_choice = input("\nChoice (1-6, default=1): ").strip() or '1'

            if view_choice == '1':
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                print(f"\n📖 Opening in less...")
                print("   💡 Controls: arrows/pgup/pgdn to scroll, / to search, q to quit")
                subprocess.call(['less', '-R', temp_path])
                os.unlink(temp_path)

            elif view_choice == '2':
                print("\n" + "─" * 80)
                print(diff_output)
                print("─" * 80)

            elif view_choice == '3':
                import tempfile, time
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                print(f"\n📖 Opening in nano... (Ctrl+X to exit)")
                time.sleep(1)
                subprocess.call(['nano', temp_path])
                os.unlink(temp_path)

            elif view_choice == '4':
                import tempfile, time
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                print(f"\n📖 Opening in vim... (:q to quit)")
                time.sleep(1)
                subprocess.call(['vim', temp_path])
                os.unlink(temp_path)

            elif view_choice == '5':
                output_file = f"conflict_{Path(filepath).name}.diff"
                with open(output_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(diff_output)
                print(f"\n✓ Saved to: {output_file}")

        print("\nNow choose resolution:")
        print(f"  O - Keep ALL as OURS   ('{ours_label}')")
        print(f"  T - Keep ALL as THEIRS ('{theirs_label}')")
        print("  B - Resolve BLOCK-BY-BLOCK")
        print("  S - Skip this file")
        choice = input("\nChoice (O/T/B/S): ").strip().upper()

    elif choice == 'F':
        diff_output = run_git_str(["diff", filepath], check=False)
        output_file = f"conflict_{Path(filepath).name}.diff"
        with open(output_file, 'w', encoding='utf-8', errors='replace') as f:
            f.write(diff_output)
        print(f"\n✓ Saved diff to: {output_file}")

        print("\nNow choose resolution:")
        print("  O - Keep ALL as OURS (local)")
        print("  T - Keep ALL as THEIRS (remote/incoming)")
        print("  B - Resolve BLOCK-BY-BLOCK")
        print("  S - Skip this file")
        choice = input("\nChoice (O/T/B/S): ").strip().upper()

    if choice == 'O':
        run_git_str(["checkout", "--ours", filepath])
        run_git_str(["add", filepath])
        print(f"✓ Kept ALL as OURS (local) in {filepath}")
        return True

    elif choice == 'T':
        run_git_str(["checkout", "--theirs", filepath])
        run_git_str(["add", filepath])
        print(f"✓ Kept ALL as THEIRS (remote/incoming) in {filepath}")
        return True

    elif choice == 'S':
        print(f"⏭️  Skipping {filepath}")
        return False

    elif choice == 'Q':
        print("👋 Quitting resolver")
        sys.exit(0)

    elif choice == 'B':
        lines = content.split('\n')
        resolved_lines = []
        last_end = -1

        for i, block in enumerate(blocks, 1):
            if last_end == -1:
                resolved_lines.extend(lines[0:block['start']])
            else:
                resolved_lines.extend(lines[last_end + 1:block['start']])

            show_conflict(filepath, i, block, len(blocks))

            while True:
                print("\nChoose resolution for this block:")
                print("  O - Keep OURS (local changes)")
                print("  T - Keep THEIRS (remote changes)")
                print("  B - Keep BOTH (ours first, then theirs)")
                print("  E - Edit manually in $EDITOR")
                print("  S - Skip this file")
                print("  Q - Quit resolver")

                block_choice = input(f"\nBlock {i}/{len(blocks)} choice (O/T/B/E/S/Q): ").strip().upper()

                if block_choice == 'O':
                    if block['ours']:
                        resolved_lines.append(block['ours'])
                    print("✓ Keeping OURS (local)")
                    break
                elif block_choice == 'T':
                    if block['theirs']:
                        resolved_lines.append(block['theirs'])
                    print("✓ Keeping THEIRS (remote)")
                    break
                elif block_choice == 'B':
                    if block['ours']:
                        resolved_lines.append(block['ours'])
                    if block['theirs']:
                        resolved_lines.append(block['theirs'])
                    print("✓ Keeping BOTH")
                    break
                elif block_choice == 'E':
                    temp_content = '\n'.join(resolved_lines + lines[block['start']:])
                    path.write_text(temp_content, encoding='utf-8', errors='replace')
                    editor = os.environ.get('EDITOR', 'nano')
                    subprocess.call([editor, filepath])
                    content = path.read_text(encoding='utf-8', errors='replace')
                    blocks_remaining = parse_conflict_blocks(content)
                    if not blocks_remaining:
                        print("✓ All conflicts resolved via editor")
                        run_git_str(["add", filepath])
                        return True
                    else:
                        print("⚠️  Still has conflicts, continuing...")
                        return resolve_conflict_interactive(filepath)
                elif block_choice == 'S':
                    print(f"⏭️  Skipping {filepath}")
                    return False
                elif block_choice == 'Q':
                    print("👋 Quitting resolver")
                    sys.exit(0)
                else:
                    print("Invalid choice, try again")

            last_end = block['end']

        # Add remaining lines after last conflict
        resolved_lines.extend(lines[last_end + 1:])

        path.write_text('\n'.join(resolved_lines), encoding='utf-8', errors='replace')
        print(f"\n✅ Resolved all conflicts in {filepath}")

        run_git_str(["add", filepath])
        print(f"✓ Staged {filepath}")

        return True

    else:
        print("Invalid choice")
        return False


def bulk_resolve_all(files: List[str], strategy: str):
    """Resolve all conflicts with a single strategy."""
    for filepath in files:
        print(f"\n📝 Resolving {filepath} with strategy: {strategy}")
        if strategy == 'ours':
            run_git_str(["checkout", "--ours", filepath])
        else:
            run_git_str(["checkout", "--theirs", filepath])
        run_git_str(["add", filepath])
        print(f"✓ {filepath}")


def run_git_interactive(args, extra_env: dict = None) -> int:
    """
    Run a git command interactively — stdin/stdout/stderr go straight to the
    terminal so the user sees output in real time and no editor can hang.
    Returns the process exit code.
    """
    import os
    env = os.environ.copy()
    # Suppress any editor prompt — commit messages are reused as-is.
    env["GIT_EDITOR"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["git"] + args,
        env=env,
        # Do NOT capture output — let it flow to the real terminal
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return result.returncode


def _complete_operation(in_rebase: bool, in_cherry_pick: bool, ours_label: str = "", theirs_label: str = "") -> bool:
    """
    After all conflicts are resolved, run git rebase --continue (or equivalent)
    and report success/failure. Returns True if operation completed cleanly.

    Uses run_git_interactive() so the process is attached to the real TTY —
    no subprocess.run(capture_output=True) that can block forever waiting for
    an editor that never appears.
    """
    if in_rebase:
        print("\n▶  Running: git rebase --continue")
        rc = run_git_interactive(["rebase", "--continue"])
        if rc == 0:
            print("✅ Rebase completed successfully!")
            return True
        else:
            # Check whether more conflicts appeared
            new_conflicts = get_conflicted_files()
            if new_conflicts:
                print("\n⚠️  The rebase has more commits with conflicts.")
                print("    Re-launching resolver for the next round...\n")
                return False  # caller will re-invoke main()
            else:
                print(f"❌ git rebase --continue exited with code {rc}.")
                print("   Run 'gitship resolve' again, or 'git rebase --abort' to reset.")
                return False

    elif in_cherry_pick:
        print("\n▶  Running: git cherry-pick --continue")
        rc = run_git_interactive(["cherry-pick", "--continue"])
        if rc == 0:
            print("✅ Cherry-pick completed successfully!")
            return True
        else:
            print(f"❌ git cherry-pick --continue exited with code {rc}.")
            return False

    else:
        # Plain merge — try --no-edit first (works when MERGE_MSG exists).
        # When MERGE_HEAD was never written (interrupted stash path) there is
        # no MERGE_MSG, so git commit --no-edit fails with "empty message".
        # In that case fall back to generating a message via merge_message.py.
        import subprocess as _sp
        repo_path = Path(".")

        merge_msg_file = Path(".git") / "MERGE_MSG"
        has_merge_msg = merge_msg_file.exists() and merge_msg_file.read_text().strip()

        if has_merge_msg:
            print("\n▶  Running: git commit --no-edit (completing merge)")
            rc = run_git_interactive(["commit", "--no-edit"])
            if rc == 0:
                print("✅ Merge committed successfully!")
                return True
            # fall through to generated message on failure

        # Generate a commit message using merge_message.generate_merge_message
        print("\n📝 No MERGE_MSG found — generating merge commit message...")
        try:
            from gitship.merge_message import generate_merge_message
        except ImportError:
            import importlib.util, os as _os
            _spec = importlib.util.spec_from_file_location(
                "merge_message",
                _os.path.join(_os.path.dirname(__file__), "merge_message.py")
            )
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            generate_merge_message = _mod.generate_merge_message

        # Determine base (our branch HEAD before merge started) and incoming
        # branch. Use ORIG_HEAD if present (set by git before a merge), else
        # fall back to HEAD. Incoming = what was being merged in (try to read
        # from staged tree / name-rev of the other parent).
        orig_head_file = Path(".git") / "ORIG_HEAD"
        if orig_head_file.exists():
            base_ref = orig_head_file.read_text().strip()
        else:
            base_ref = _sp.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True
            ).stdout.strip()

        # Try to find the incoming SHA from the merge parents in the index
        # (git write-tree captures the staged merge state).
        head_ref = _sp.run(
            ["git", "rev-parse", "MERGE_HEAD"],
            capture_output=True, text=True
        ).stdout.strip()
        if not head_ref:
            # MERGE_HEAD is gone; use the tip of the branch that was being
            # merged. Try to infer from stash or staged diff base.
            head_ref = _sp.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True
            ).stdout.strip()

        try:
            msg = generate_merge_message(repo_path, base_ref=base_ref, head_ref=head_ref)
            # Patch the first line to use human-readable branch names if we have them
            if ours_label and theirs_label:
                msg_lines = msg.splitlines()
                msg_lines[0] = f"Merge {theirs_label} → {ours_label}"
                msg = "\n".join(msg_lines)
        except Exception as e:
            src = theirs_label or "development"
            tgt = ours_label or "developer-port"
            msg = f"Merge {src} → {tgt} (resolved conflicts)\n\nAuto-generated after manual conflict resolution.\n\nError generating detailed message: {e}"

        print("\n📋 Generated commit message:")
        print("─" * 60)
        print(msg[:800] + ("..." if len(msg) > 800 else ""))
        print("─" * 60)

        edit = input("\nEdit message before committing? (y/n, default=n): ").strip().lower()
        if edit == "y":
            import tempfile, os as _os, subprocess as _sp2
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
                tf.write(msg)
                tmp = tf.name
            editor = _os.environ.get("VISUAL") or _os.environ.get("EDITOR") or "nano"
            _sp2.call([editor, tmp])
            msg = open(tmp).read()
            _os.unlink(tmp)

        print("\n▶  Running: git commit -m <generated message>")
        rc = run_git_interactive(["commit", "-m", msg])
        if rc == 0:
            print("✅ Merge committed successfully!")
            return True
        else:
            print(f"❌ git commit exited with code {rc}.")
            return False


def main():
    git_dir = Path(".git")
    in_rebase = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
    in_merge = (git_dir / "MERGE_HEAD").exists()
    in_cherry_pick = (git_dir / "CHERRY_PICK_HEAD").exists()

    if not in_rebase and not in_merge and not in_cherry_pick:
        # Fallback 1: conflict markers still present (interrupted stash path)
        if get_conflicted_files():
            in_merge = True  # treat as plain merge: resolve + git commit
        else:
            # Fallback 2: conflicts already resolved & staged but no MERGE_HEAD
            # and no MERGE_MSG — happens when a previous run resolved everything
            # but the commit step failed (e.g. empty message).
            # Detect via: staged changes exist + ORIG_HEAD exists (set by git
            # before it started the merge, even in the broken path).
            import subprocess as _sp2
            _staged = _sp2.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True
            ).stdout.strip()
            _orig_head = (git_dir / "ORIG_HEAD").exists()
            if _staged and _orig_head:
                in_merge = True  # staged merge ready to commit, just needs a message
            else:
                print("❌ Not in a rebase, merge, or cherry-pick state")
                sys.exit(1)

    # Read current branch and merge source for display
    import subprocess as _sp
    _cur = _sp.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    _merge_head_file = git_dir / "MERGE_HEAD"
    _merge_source = None
    if _merge_head_file.exists():
        _merge_sha = _merge_head_file.read_text().strip()
        # Try to resolve SHA to a branch name
        _name_res = _sp.run(["git", "name-rev", "--name-only", "--no-undefined", _merge_sha],
                             capture_output=True, text=True)
        if _name_res.returncode == 0:
            _merge_source = _name_res.stdout.strip().replace("remotes/origin/", "").split("~")[0]

    ours_label = _cur or "your branch (developer-port)"
    # If both labels would be identical (e.g. merging origin/development into
    # local development), make it obvious which is local and which is remote.
    if _merge_source and _merge_source == _cur:
        theirs_label = f"origin/{_merge_source} (remote)"
    else:
        theirs_label = _merge_source or "incoming branch (development)"

    # Describe what we're in
    if in_rebase:
        # Try to read the branch name for a nicer message
        branch = None
        for state_dir in ["rebase-merge", "rebase-apply"]:
            head_name_file = git_dir / state_dir / "head-name"
            if head_name_file.exists():
                ref = head_name_file.read_text(encoding='utf-8', errors='replace').strip()
                branch = ref.replace("refs/heads/", "")
                break
        label = f"rebase of '{branch}'" if branch else "rebase"
    elif in_cherry_pick:
        label = "cherry-pick"
    else:
        label = f"merge  (OURS='{ours_label}'  ←  THEIRS='{theirs_label}')"

    files = get_conflicted_files()

    if not files:
        # No conflict markers — the operation just needs to be continued
        print(f"✅ No conflicted files found! The {label} is ready to commit.")
        auto = input(f"\nCommit the merge now? (y/n, default=y): ").strip().lower() or 'y'
        if auto == 'y':
            _complete_operation(in_rebase, in_cherry_pick, ours_label=ours_label, theirs_label=theirs_label)
        else:
            if in_rebase:
                print("   When ready, run: git rebase --continue")
            elif in_cherry_pick:
                print("   When ready, run: git cherry-pick --continue")
            else:
                print("   When ready, run: git commit")
        sys.exit(0)

    print(f"\n🔀 Found {len(files)} file(s) with conflicts ({label}):")
    for f in files:
        print(f"  - {f}")

    print("\n" + "=" * 80)
    print("CONFLICT RESOLUTION OPTIONS")
    print("=" * 80)
    print(f"  🔵 OURS   = '{ours_label}'  (your branch, local changes)")
    print(f"  🔴 THEIRS = '{theirs_label}'  (incoming branch, their changes)")
    print("\n1. INTERACTIVE - Resolve each conflict individually (recommended)")
    print(f"2. BULK OURS   - Keep '{ours_label}' in all conflicts")
    print(f"3. BULK THEIRS - Keep '{theirs_label}' in all conflicts")
    print("4. ABORT       - Abort the operation")

    choice = input("\nChoice (1-4): ").strip()

    if choice == '1':
        for filepath in files:
            resolve_conflict_interactive(filepath, ours_label=ours_label, theirs_label=theirs_label)

        remaining = get_conflicted_files()
        if remaining:
            print(f"\n⚠️  Still have {len(remaining)} unresolved file(s):")
            for f in remaining:
                print(f"  - {f}")
            print("Resolve them, stage with 'git add <file>', then run 'gitship resolve' again.")
            sys.exit(1)
        else:
            print("\n🎉 All conflicts resolved!")
            # ── AUTO-CONTINUE ─────────────────────────────────────────────
            auto = input(f"\nContinue the {label} now? (y/n, default=y): ").strip().lower() or 'y'
            if auto == 'y':
                ok = _complete_operation(in_rebase, in_cherry_pick, ours_label=ours_label, theirs_label=theirs_label)
                if not ok and (in_rebase or in_cherry_pick):
                    # More rounds needed — recurse
                    new_files = get_conflicted_files()
                    if new_files:
                        print("\n🔁 Re-entering resolver for next round of conflicts...")
                        main()
            else:
                if in_rebase:
                    print("   When ready, run: git rebase --continue")
                elif in_cherry_pick:
                    print("   When ready, run: git cherry-pick --continue")
                else:
                    print("   When ready, run: git commit")

    elif choice == '2':
        print("\n⚠️  Keeping ALL local changes (OURS)")
        bulk_resolve_all(files, 'ours')
        print("\n✅ All conflicts resolved with OURS")
        auto = input(f"\nContinue the {label} now? (y/n, default=y): ").strip().lower() or 'y'
        if auto == 'y':
            _complete_operation(in_rebase, in_cherry_pick, ours_label=ours_label, theirs_label=theirs_label)
        else:
            if in_rebase:
                print("   When ready, run: git rebase --continue")
            elif in_cherry_pick:
                print("   When ready, run: git cherry-pick --continue")
            else:
                print("   When ready, run: git commit")

    elif choice == '3':
        print("\n⚠️  Keeping ALL remote changes (THEIRS)")
        bulk_resolve_all(files, 'theirs')
        print("\n✅ All conflicts resolved with THEIRS")
        auto = input(f"\nContinue the {label} now? (y/n, default=y): ").strip().lower() or 'y'
        if auto == 'y':
            _complete_operation(in_rebase, in_cherry_pick, ours_label=ours_label, theirs_label=theirs_label)
        else:
            if in_rebase:
                print("   When ready, run: git rebase --continue")
            elif in_cherry_pick:
                print("   When ready, run: git cherry-pick --continue")
            else:
                print("   When ready, run: git commit")

    elif choice == '4':
        confirm = input(f"\n⚠️  Really abort the {label}? This will undo all conflict resolutions. (y/n): ").strip().lower()
        if confirm == 'y':
            if in_rebase:
                run_git_interactive(["rebase", "--abort"])
                print("✓ Rebase aborted — you are back to your pre-rebase state")
            elif in_cherry_pick:
                run_git_interactive(["cherry-pick", "--abort"])
                print("✓ Cherry-pick aborted")
            else:
                run_git_interactive(["merge", "--abort"])
                print("✓ Merge aborted")
        else:
            print("Abort cancelled — nothing changed.")
    else:
        print("Invalid choice")


if __name__ == "__main__":
    main()