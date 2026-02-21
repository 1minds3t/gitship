"""
gitship vscode_history - Restore files from VSCode's local edit history.

Can be used two ways:
  1. Standalone:  gitship vscode-history [directory]
  2. As a helper: called by gitship init when commits fail due to missing blobs,
     to check if VSCode has a recoverable version before giving up.

Safety philosophy:
  - Empty Enter always SKIPS (never restores). Accidental Enter = safe.
  - Restore requires an explicit 'y' confirmation every time.
  - Dry-run mode available (--dry-run) to preview without touching anything.
  - Every overwrite gets a .bak backup first (unless --no-backup).
"""

import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
import hashlib


# ── Utility ────────────────────────────────────────────────────────────────────

def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode())


def _file_hash(path: Path) -> str | None:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()
    except Exception:
        return None


def _history_base() -> Path:
    """Return the VSCode history directory, respecting sudo."""
    if "SUDO_USER" in os.environ:
        import pwd
        original_home = pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir
        base = Path(original_home) / ".config/Code/User/History"
        _safe_print(f"  🔓 Running as sudo — using {os.environ['SUDO_USER']}'s VSCode history")
    else:
        base = Path.home() / ".config/Code/User/History"
    return base


# ── Core scanner ───────────────────────────────────────────────────────────────

class VSCodeHistory:
    def __init__(self, target_dir: Path | None = None):
        self.history_base = _history_base()
        self.target_dir = Path(target_dir).resolve() if target_dir else Path.cwd()
        self.file_versions: dict[str, list[dict]] = defaultdict(list)
        self._scanned = False

    def scan(self) -> int:
        """
        Scan VSCode history for files under target_dir.
        Returns number of files found.
        """
        _safe_print(f"  🔍 Scanning VSCode history for: {self.target_dir}")

        if not self.history_base.exists():
            _safe_print("  ⚠️  VSCode history directory not found — is VSCode installed?")
            return 0

        target_uri = self.target_dir.as_uri() + "/"

        for entries_file in self.history_base.glob("*/entries.json"):
            try:
                with open(entries_file) as f:
                    data = json.load(f)

                resource = unquote(data.get("resource", ""))
                if not resource.startswith(target_uri):
                    continue
                # Skip backup/staging dirs we created ourselves
                if any(x in resource.lower() for x in ["backup", "safety_backup", ".bak"]):
                    continue

                rel_path = resource.replace(target_uri, "")

                for entry in data.get("entries", []):
                    history_file = entries_file.parent / entry["id"]
                    if not history_file.exists():
                        continue
                    self.file_versions[rel_path].append({
                        "timestamp": entry.get("timestamp", 0),
                        "datetime":  datetime.fromtimestamp(entry.get("timestamp", 0) / 1000),
                        "source":    history_file,
                        "dest":      self.target_dir / rel_path,
                        "hash":      None,
                    })
            except Exception:
                continue

        # Sort newest-first, deduplicate by content hash
        for rel_path, versions in self.file_versions.items():
            versions.sort(key=lambda x: x["timestamp"], reverse=True)
            seen: set[str] = set()
            unique = []
            for ver in versions:
                h = _file_hash(ver["source"])
                if h and h not in seen:
                    seen.add(h)
                    ver["hash"] = h
                    unique.append(ver)
            self.file_versions[rel_path] = unique

        self._scanned = True
        count = len(self.file_versions)
        _safe_print(f"  ✓ Found {count} file(s) with VSCode history\n")
        return count

    # ── Helper API (called by init.py) ─────────────────────────────────────────

    def scan_for_missing(self, missing_paths: list[Path]) -> dict[str, list[dict]]:
        """
        Targeted scan: given a list of absolute paths that git couldn't commit,
        return only the VSCode history entries that match those files.
        No interactive prompts. No side effects.

        Returns: { rel_path_str: [version_dicts, ...] }
        """
        if not self._scanned:
            self.scan()

        results = {}
        for p in missing_paths:
            try:
                rel = str(p.resolve().relative_to(self.target_dir))
            except ValueError:
                rel = str(p)
            if rel in self.file_versions:
                results[rel] = self.file_versions[rel]
        return results

    # ── Diff / preview ─────────────────────────────────────────────────────────

    def _diff_stat(self, current: Path, history: Path) -> str:
        try:
            result = subprocess.run(
                ["diff", "-u", str(current), str(history)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return "identical"
            lines = result.stdout.splitlines()
            adds = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            dels = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            return f"+{adds} -{dels}"
        except Exception:
            return "?"

    def _show_diff(self, current: Path, history: Path, context: int = 3):
        try:
            result = subprocess.run(
                ["diff", "-u", f"-U{context}", "--color=always", str(current), str(history)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                _safe_print("  ℹ️  Files are identical")
                return
            lines = result.stdout.splitlines()
            for line in lines[:60]:
                print(f"  {line}")
            if len(lines) > 60:
                print(f"  ... ({len(lines) - 60} more lines)")
        except FileNotFoundError:
            _safe_print("  ⚠️  diff not available")
        except Exception as e:
            _safe_print(f"  ⚠️  Diff error: {e}")

    def _preview(self, version: dict, lines: int = 20):
        try:
            with open(version["source"], encoding="utf-8", errors="ignore") as f:
                content = f.readlines()
            _safe_print(f"\n  📋 Preview (first {lines} lines):")
            _safe_print("  " + "─" * 58)
            for i, line in enumerate(content[:lines], 1):
                print(f"  {i:3d} | {line.rstrip()}")
            if len(content) > lines:
                print(f"  ... ({len(content) - lines} more lines)")
            _safe_print("  " + "─" * 58)
        except Exception as e:
            _safe_print(f"  ⚠️  Preview failed: {e}")

    # ── Restore ────────────────────────────────────────────────────────────────

    def restore(self, rel_path: str, version: dict, backup: bool = True, dry_run: bool = False) -> bool:
        dest = version["dest"]

        if dry_run:
            _safe_print(f"  [dry-run] Would restore: {rel_path}  ←  {version['datetime'].strftime('%Y-%m-%d %H:%M:%S')}")
            return True

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            _safe_print(f"  ✗ Permission denied: {dest.parent}")
            return False

        if backup and dest.exists():
            bak = dest.with_suffix(dest.suffix + ".bak")
            try:
                shutil.copy2(dest, bak)
                _safe_print(f"  💾 Backed up → {bak.name}")
            except Exception as e:
                _safe_print(f"  ⚠️  Backup failed ({e}), continuing anyway...")

        try:
            shutil.copy2(version["source"], dest)
            _safe_print(f"  ✓ Restored: {rel_path}")
            return True
        except PermissionError:
            _safe_print(f"  ✗ Permission denied writing: {dest}")
            return False

    # ── Interactive mode ────────────────────────────────────────────────────────

    def interactive_restore(self, backup: bool = True, dry_run: bool = False):
        """
        Walk through each file that has VSCode history and offer to restore.

        Safety: empty Enter = Skip (never auto-restores).
        """
        if not self._scanned:
            self.scan()

        if not self.file_versions:
            _safe_print("  ✗ No VSCode history found for this directory.")
            return

        if dry_run:
            _safe_print("  ℹ️  DRY RUN — nothing will be written.\n")

        print("=" * 60)
        print("  GITSHIP — VSCode History Restore")
        print("=" * 60)
        print(f"  Directory : {self.target_dir}")
        print(f"  Files found: {len(self.file_versions)}")
        print()

        sorted_files = sorted(self.file_versions.items())
        restored = 0
        skipped = 0

        for rel_path, versions in sorted_files:
            current = self.target_dir / rel_path

            print("─" * 60)
            print(f"  📄 {rel_path}")

            if current.exists():
                mtime = datetime.fromtimestamp(current.stat().st_mtime)
                print(f"     Current : {mtime.strftime('%Y-%m-%d %H:%M:%S')} (on disk)")
            else:
                print("     Current : ✗ not on disk")

            print(f"     Versions: {len(versions)} unique snapshots in VSCode history")
            print()

            # Show version list with diff stats
            for i, ver in enumerate(versions, 1):
                dt = ver["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                stat = self._diff_stat(current, ver["source"]) if current.exists() else "—"
                print(f"     [{i}] {dt}   {stat}")

            print()
            print("  Options:  [1-N] pick version   [D]iff   [P]review   [S]kip   [Q]uit")
            print("  ⚠️   Enter alone = Skip (nothing happens)")
            print()

            while True:
                try:
                    raw = input("  Choice: ").strip()
                except KeyboardInterrupt:
                    print("\n\n  Interrupted. Goodbye!")
                    return

                choice = raw.upper()

                if choice in ("", "S"):
                    _safe_print("  ↷ Skipped\n")
                    skipped += 1
                    break

                elif choice == "Q":
                    _safe_print(f"\n  Done. Restored {restored}, skipped {skipped}.")
                    return

                elif choice == "D":
                    if not current.exists():
                        _safe_print("  ⚠️  No current file to diff against.")
                        continue
                    try:
                        n = int(input("  Diff which version? [1]: ").strip() or "1") - 1
                        if 0 <= n < len(versions):
                            self._show_diff(current, versions[n]["source"])
                        else:
                            _safe_print("  ⚠️  Out of range.")
                    except ValueError:
                        _safe_print("  ⚠️  Enter a number.")
                    continue

                elif choice == "P":
                    try:
                        n = int(input("  Preview which version? [1]: ").strip() or "1") - 1
                        if 0 <= n < len(versions):
                            self._preview(versions[n])
                        else:
                            _safe_print("  ⚠️  Out of range.")
                    except ValueError:
                        _safe_print("  ⚠️  Enter a number.")
                    continue

                else:
                    # Numeric version pick
                    try:
                        idx = int(choice) - 1
                        if not (0 <= idx < len(versions)):
                            _safe_print(f"  ⚠️  Enter 1–{len(versions)}.")
                            continue
                    except ValueError:
                        _safe_print("  ⚠️  Unknown option. Enter a number, D, P, S, or Q.")
                        continue

                    ver = versions[idx]
                    dt = ver["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                    print(f"\n  Restore version from {dt}?")
                    confirm = input("  Type 'y' to confirm, anything else to cancel: ").strip().lower()
                    if confirm == "y":
                        if self.restore(rel_path, ver, backup=backup, dry_run=dry_run):
                            restored += 1
                    else:
                        _safe_print("  ↷ Cancelled\n")
                    break

        print("─" * 60)
        _safe_print(f"\n  ✓ Done. Restored {restored} file(s), skipped {skipped}.")


# ── init.py integration helper ─────────────────────────────────────────────────

def offer_restore_for_missing(repo_path: Path, failed_files: list[Path], backup: bool = True) -> list[Path]:
    """
    Called by gitship init when git commit fails with 'invalid object' errors.

    Scans VSCode history for each failed file, presents a per-file confirmation
    prompt, and restores chosen versions in-place so the commit can be retried.

    Returns list of paths that were successfully restored.
    """
    restorer = VSCodeHistory(target_dir=repo_path)
    matches = restorer.scan_for_missing(failed_files)

    if not matches:
        _safe_print("  ℹ️  No VSCode history found for the affected files.")
        return []

    print()
    _safe_print(f"  💡 VSCode history found for {len(matches)} of the missing file(s):")
    for rel in sorted(matches):
        versions = matches[rel]
        latest = versions[0]
        _safe_print(f"     • {rel}  (latest snapshot: {latest['datetime'].strftime('%Y-%m-%d %H:%M:%S')})")

    print()
    print("  Restore these files from VSCode history and retry the commit? [y/N]: ", end="")
    try:
        answer = input().strip().lower()
    except KeyboardInterrupt:
        print()
        return []

    if answer != "y":
        _safe_print("  ↷ Skipped VSCode restore.")
        return []

    restored_paths = []
    for rel_path, versions in sorted(matches.items()):
        ver = versions[0]
        dest = repo_path / rel_path
        print()
        _safe_print(f"  📄 {rel_path}")
        _safe_print(f"     Snapshot: {ver['datetime'].strftime('%Y-%m-%d %H:%M:%S')}")

        # Show a quick preview so user knows what they're getting
        confirm = input("     Restore this version? [y/N]: ").strip().lower()
        if confirm == "y":
            if restorer.restore(rel_path, ver, backup=backup):
                restored_paths.append(dest)
        else:
            _safe_print("     ↷ Skipped")

    return restored_paths


# ── CLI entry point ─────────────────────────────────────────────────────────────

def main_with_repo(repo_path: Path):
    """Entry point called by gitship CLI / menu."""
    restorer = VSCodeHistory(target_dir=repo_path)
    restorer.scan()
    restorer.interactive_restore(backup=True, dry_run=False)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Restore files from VSCode local history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gitship vscode-history                  # Interactive restore in current dir
  gitship vscode-history ~/myproject      # Interactive restore for specific dir
  gitship vscode-history --dry-run        # Preview what would be restored
  gitship vscode-history --list           # List files with history and exit
  gitship vscode-history --no-backup      # Restore without .bak files
        """
    )
    parser.add_argument("directory", nargs="?", default=".",
                        help="Target directory (default: current directory)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be restored without writing anything")
    parser.add_argument("--list", action="store_true",
                        help="List files with available history and exit")
    parser.add_argument("--no-backup", action="store_true",
                        help="Don't create .bak files before overwriting")

    args = parser.parse_args()

    restorer = VSCodeHistory(target_dir=Path(args.directory))
    restorer.scan()

    if args.list:
        if not restorer.file_versions:
            _safe_print("  No VSCode history found for this directory.")
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


if __name__ == "__main__":
    main()
