#!/usr/bin/env python3
"""
mkdocs_deploy - MkDocs deployment helpers for gitship.

Handles:
  - Safe port finding (concurrent-safe, no race conditions)
  - Serving docs locally via mkdocs serve / python http.server / caddy
  - systemd user-service setup so docs survive reboots (Linux/macOS)
  - GitHub Actions workflow for gh-pages deployment
  - GitHub Pages activation via gh CLI
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

# ── colour helpers (same pattern as pypi.py) ──────────────────────────────────

class C:
    RESET  = '\033[0m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    RED    = '\033[31m'
    GREEN  = '\033[32m'
    YELLOW = '\033[33m'
    BLUE   = '\033[34m'
    CYAN   = '\033[36m'
    BGREEN = '\033[92m'
    BYELLOW= '\033[93m'
    BCYAN  = '\033[96m'

def _h(text: str) -> str:
    """Heading line."""
    return f"{C.BOLD}{text}{C.RESET}"

def _ok(text: str) -> str:  return f"{C.GREEN}✓ {text}{C.RESET}"
def _warn(text: str) -> str: return f"{C.YELLOW}⚠ {text}{C.RESET}"
def _err(text: str) -> str:  return f"{C.RED}✗ {text}{C.RESET}"
def _dim(text: str) -> str:  return f"{C.DIM}{text}{C.RESET}"


# ── port finder (adapted from flask_port_finder.py) ───────────────────────────

_port_lock      = threading.Lock()
_reserved_ports: set = set()


def _is_port_free(port: int) -> bool:
    """Actually try to bind the port — no false positives."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _reserve(port: int, ttl: float = 15.0) -> bool:
    with _port_lock:
        if port in _reserved_ports:
            return False
        _reserved_ports.add(port)

    def _release():
        time.sleep(ttl)
        with _port_lock:
            _reserved_ports.discard(port)

    threading.Thread(target=_release, daemon=True).start()
    return True


def find_free_port(start: int = 8000, max_scan: int = 200) -> int:
    """
    Find a free port starting from `start`, skipping reserved ones.
    Thread-safe — same algorithm as flask_port_finder.py.
    """
    for port in range(start, start + max_scan):
        with _port_lock:
            if port in _reserved_ports:
                continue
        if not _is_port_free(port):
            continue
        if _reserve(port):
            return port
    raise RuntimeError(f"No free port found in {start}–{start + max_scan}")


# ── tool detection ─────────────────────────────────────────────────────────────

def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _mkdocs_available() -> bool:
    return _cmd_exists("mkdocs")


def _caddy_available() -> bool:
    return _cmd_exists("caddy")


def _systemd_available() -> bool:
    """True on Linux with systemd --user support."""
    if sys.platform == "win32":
        return False
    return _cmd_exists("systemctl") and _cmd_exists("systemd-run")


def _gh_available() -> bool:
    return _cmd_exists("gh")


def _git_available() -> bool:
    return _cmd_exists("git")


# ── git / github helpers (same pattern as pypi.py) ────────────────────────────

def _run(args: list, cwd: Path = None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=capture, text=True, check=False)


def get_github_info(repo_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Return (owner, repo) from git remote origin."""
    r = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    if r.returncode != 0:
        return None, None
    url = r.stdout.strip().removesuffix(".git")
    # HTTPS: https://github.com/owner/repo
    if url.startswith("https://"):
        parts = url.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    # SSH: git@github.com:owner/repo
    if ":" in url:
        after = url.split(":", 1)[1]
        parts = after.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None, None


def get_default_branch(repo_path: Path) -> str:
    r = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
    if r.returncode == 0:
        return r.stdout.strip().split("/")[-1]
    return "main"


# ── GitHub Actions workflow generator ─────────────────────────────────────────

def generate_ghpages_workflow(default_branch: str = "main") -> str:
    """
    Generate a GitHub Actions workflow that builds MkDocs and deploys
    to the gh-pages branch on every push to the default branch.
    Mirrors the pattern used in pypi.py — self-contained, OIDC-safe.
    """
    return f"""\
name: Deploy docs to GitHub Pages

on:
  push:
    branches: ["{default_branch}"]
  workflow_dispatch:

permissions:
  contents: write

jobs:
  deploy:
    name: Build and deploy MkDocs
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0          # needed for git-revision-date plugin

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.x"

      - name: Install MkDocs + theme
        run: |
          python -m pip install --upgrade pip
          # Install from requirements-docs.txt if present, otherwise bare minimum
          if [ -f requirements-docs.txt ]; then
            pip install -r requirements-docs.txt
          elif [ -f requirements.txt ]; then
            pip install mkdocs mkdocs-material || pip install mkdocs
          else
            pip install mkdocs mkdocs-material || pip install mkdocs
          fi

      - name: Deploy to GitHub Pages
        run: mkdocs gh-deploy --force --clean --verbose
"""


def ensure_ghpages_workflow(repo_path: Path, force: bool = False) -> bool:
    """
    Write .github/workflows/docs.yml if it doesn't exist (or force=True).
    Returns True if the file is now in place.
    """
    wf_dir = repo_path / ".github" / "workflows"
    wf_file = wf_dir / "docs.yml"

    if wf_file.exists() and not force:
        print(_ok("docs.yml workflow already exists"))
        return True

    default_branch = get_default_branch(repo_path)
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file.write_text(generate_ghpages_workflow(default_branch))
    _run(["git", "add", str(wf_file)], cwd=repo_path)
    print(_ok(f"Created {wf_file.relative_to(repo_path)}"))
    return True


# ── GitHub Pages activation ────────────────────────────────────────────────────

def enable_github_pages(repo_path: Path) -> bool:
    """
    Enable GitHub Pages (gh-pages branch) via gh CLI API.
    Returns True on success.
    """
    if not _gh_available():
        print(_warn("gh CLI not found — please enable Pages manually"))
        print(_dim("  Settings → Pages → Source: Deploy from branch → gh-pages"))
        return False

    owner, repo = get_github_info(repo_path)
    if not owner:
        print(_warn("Could not detect GitHub remote"))
        return False

    print(f"\n{C.CYAN}Enabling GitHub Pages (gh-pages branch)…{C.RESET}")
    r = _run([
        "gh", "api",
        "--method", "POST",
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2022-11-28",
        f"/repos/{owner}/{repo}/pages",
        "-f", "source[branch]=gh-pages",
        "-f", "source[path]=/"
    ], cwd=repo_path)

    if r.returncode == 0:
        print(_ok("GitHub Pages enabled"))
        print(_dim(f"  URL will be: https://{owner}.github.io/{repo}/"))
        return True

    # 409 = already configured — that's fine
    if "already" in (r.stderr or "").lower() or r.returncode == 0:
        print(_ok("GitHub Pages already configured"))
        return True

    print(_warn(f"Could not auto-enable Pages (you may need to do it manually)"))
    print(_dim(f"  https://github.com/{owner}/{repo}/settings/pages"))
    print(_dim(f"  stderr: {r.stderr.strip()[:200]}"))
    return False


# ── local server helpers ───────────────────────────────────────────────────────

MKDOCS_SERVICE = "gitship-mkdocs"   # systemd unit name stem


def _serve_mkdocs_foreground(repo_path: Path, port: int):
    """Run mkdocs serve in the foreground (blocks). For quick previews."""
    print(f"\n{C.CYAN}Starting mkdocs serve on port {port}…{C.RESET}")
    print(_dim("  Ctrl+C to stop"))
    try:
        subprocess.run(
            ["mkdocs", "serve", "--dev-addr", f"127.0.0.1:{port}"],
            cwd=repo_path
        )
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Stopped.{C.RESET}")


def _serve_http_foreground(repo_path: Path, port: int):
    """
    Fall back to python -m http.server from the site/ build directory.
    Requires the docs to have been built first with mkdocs build.
    """
    site_dir = repo_path / "site"
    if not site_dir.exists():
        print(f"{C.CYAN}Building docs first (mkdocs build)…{C.RESET}")
        r = _run(["mkdocs", "build", "--site-dir", str(site_dir)], cwd=repo_path, capture=False)
        if r.returncode != 0:
            print(_err("mkdocs build failed"))
            return

    print(f"\n{C.CYAN}Serving {site_dir} on http://127.0.0.1:{port}{C.RESET}")
    print(_dim("  Ctrl+C to stop"))
    try:
        subprocess.run(
            [sys.executable, "-m", "http.server", str(port)],
            cwd=site_dir
        )
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Stopped.{C.RESET}")


def _choose_server_backend(repo_path: Path) -> str:
    """
    Pick the best available backend: mkdocs → caddy → http.server.
    Returns one of: 'mkdocs', 'caddy', 'http'
    """
    if _mkdocs_available():
        return "mkdocs"
    if _caddy_available():
        return "caddy"
    return "http"


def _serve_caddy_foreground(repo_path: Path, port: int):
    """Serve with Caddy (static file server) from site/."""
    site_dir = repo_path / "site"
    if not site_dir.exists():
        if _mkdocs_available():
            print(f"{C.CYAN}Building docs first…{C.RESET}")
            _run(["mkdocs", "build", "--site-dir", str(site_dir)], cwd=repo_path, capture=False)
        else:
            print(_err("No site/ directory and mkdocs not available to build it"))
            return

    caddyfile = f":{{port}} {{\n  root * {site_dir}\n  file_server\n}}"
    tmp = Path("/tmp/gitship_Caddyfile")
    tmp.write_text(caddyfile.format(port=port))
    print(f"\n{C.CYAN}Serving via Caddy on http://127.0.0.1:{port}{C.RESET}")
    print(_dim("  Ctrl+C to stop"))
    try:
        subprocess.run(["caddy", "run", "--config", str(tmp)])
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Stopped.{C.RESET}")
    finally:
        tmp.unlink(missing_ok=True)


# ── systemd service management ────────────────────────────────────────────────

def _unit_name(port: int) -> str:
    return f"{MKDOCS_SERVICE}-{port}.service"


def _systemd_user_dir() -> Path:
    """~/.config/systemd/user/"""
    return Path.home() / ".config" / "systemd" / "user"


def _build_unit(repo_path: Path, port: int, backend: str) -> str:
    """
    Generate a systemd user-service unit file.
    Uses the chosen backend (mkdocs / http / caddy).
    """
    if backend == "mkdocs":
        exec_start = (
            f"{shutil.which('mkdocs') or 'mkdocs'} serve "
            f"--dev-addr 127.0.0.1:{port}"
        )
    elif backend == "caddy":
        site_dir = repo_path / "site"
        exec_start = (
            f"{shutil.which('caddy') or 'caddy'} file-server "
            f"--root {site_dir} --listen :{port}"
        )
    else:  # http.server
        site_dir = repo_path / "site"
        exec_start = f"{sys.executable} -m http.server {port}"

    working_dir = str(repo_path) if backend in ("mkdocs",) else str(repo_path / "site")

    return f"""\
[Unit]
Description=MkDocs docs server — {repo_path.name} on :{port}
After=network.target

[Service]
Type=simple
WorkingDirectory={working_dir}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_systemd_service(repo_path: Path, port: int, backend: str) -> bool:
    """
    Install and enable a systemd --user service to keep docs alive.
    Returns True on success.
    """
    if not _systemd_available():
        print(_warn("systemd not available on this system"))
        return False

    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_file = unit_dir / _unit_name(port)

    unit_content = _build_unit(repo_path, port, backend)
    unit_file.write_text(unit_content)
    print(_ok(f"Unit file written: {unit_file}"))

    steps = [
        (["systemctl", "--user", "daemon-reload"],          "Reloaded daemon"),
        (["systemctl", "--user", "enable", _unit_name(port)], "Service enabled (survives reboot)"),
        (["systemctl", "--user", "start",  _unit_name(port)], f"Service started on :{port}"),
    ]
    for cmd, msg in steps:
        r = _run(cmd)
        if r.returncode != 0:
            print(_err(f"Command failed: {' '.join(cmd)}"))
            print(_dim(f"  {r.stderr.strip()[:300]}"))
            return False
        print(_ok(msg))

    # linger — keep service alive after logout
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if username:
        _run(["loginctl", "enable-linger", username])
        print(_ok(f"Linger enabled for {username} (service survives logout)"))

    print(f"\n{C.BGREEN}Docs live at: http://127.0.0.1:{port}{C.RESET}")
    print(_dim("  Point nginx / Tailscale / Caddy reverse-proxy at that address."))
    return True


def remove_systemd_service(port: int) -> bool:
    """Stop and remove the systemd service for a given port."""
    if not _systemd_available():
        return False

    unit = _unit_name(port)
    unit_file = _systemd_user_dir() / unit

    for cmd in [
        ["systemctl", "--user", "stop",    unit],
        ["systemctl", "--user", "disable", unit],
    ]:
        _run(cmd)

    if unit_file.exists():
        unit_file.unlink()
        print(_ok(f"Removed {unit_file.name}"))

    _run(["systemctl", "--user", "daemon-reload"])
    print(_ok(f"Service on :{port} removed"))
    return True


def list_systemd_services() -> list:
    """Return list of active gitship mkdocs services as dicts."""
    if not _systemd_available():
        return []

    unit_dir = _systemd_user_dir()
    services = []
    for f in unit_dir.glob(f"{MKDOCS_SERVICE}-*.service"):
        port_str = f.stem.replace(f"{MKDOCS_SERVICE}-", "")
        try:
            port = int(port_str)
        except ValueError:
            continue
        r = _run(["systemctl", "--user", "is-active", f.name])
        status = r.stdout.strip()
        services.append({"file": f, "port": port, "status": status})
    return services


# ── mkdocs.yml presence / stub ────────────────────────────────────────────────

def ensure_mkdocs_yml(repo_path: Path) -> bool:
    """
    If mkdocs.yml is missing, offer to create a minimal one.
    Returns True if file now exists.
    """
    mkdocs_yml = repo_path / "mkdocs.yml"
    if mkdocs_yml.exists():
        return True

    print(_warn("mkdocs.yml not found in this repo"))
    ans = input("Create a minimal mkdocs.yml now? [Y/n]: ").strip().lower()
    if ans in ("", "y", "yes"):
        project_name = repo_path.name
        stub = f"""\
site_name: {project_name}
site_description: Documentation for {project_name}
theme:
  name: material       # or: readthedocs / mkdocs
docs_dir: docs
site_dir: site
"""
        mkdocs_yml.write_text(stub)
        # Also ensure docs/ with an index exists
        docs_dir = repo_path / "docs"
        docs_dir.mkdir(exist_ok=True)
        index = docs_dir / "index.md"
        if not index.exists():
            index.write_text(f"# {project_name}\n\nWelcome to the documentation.\n")
            print(_ok(f"Created docs/index.md"))
        print(_ok(f"Created mkdocs.yml"))
        return True
    return False


# ── GitHub Pages interactive flow ─────────────────────────────────────────────

def flow_github_pages(repo_path: Path):
    """
    Interactive wizard:
    1. Ensure mkdocs.yml exists
    2. Create / show .github/workflows/docs.yml
    3. Activate GitHub Pages via gh CLI
    4. Offer to commit + push immediately
    """
    print(f"\n{_h('=' * 60)}")
    print(_h("🚀  GITHUB PAGES DEPLOYMENT"))
    print(_h("=" * 60))

    if not ensure_mkdocs_yml(repo_path):
        print(_err("Cannot proceed without mkdocs.yml"))
        return

    # Workflow
    wf_dir = repo_path / ".github" / "workflows"
    wf_file = wf_dir / "docs.yml"
    if wf_file.exists():
        print(_ok("docs.yml workflow already exists"))
        recreate = input("Re-create it from template? [y/N]: ").strip().lower()
        if recreate in ("y", "yes"):
            ensure_ghpages_workflow(repo_path, force=True)
    else:
        ensure_ghpages_workflow(repo_path)

    # Activate Pages
    if _gh_available():
        activate = input("\nActivate GitHub Pages via gh CLI now? [Y/n]: ").strip().lower()
        if activate in ("", "y", "yes"):
            enable_github_pages(repo_path)
    else:
        print(_warn("gh CLI not installed — activate Pages manually:"))
        owner, repo = get_github_info(repo_path)
        if owner:
            print(_dim(f"  https://github.com/{owner}/{repo}/settings/pages"))

    # Commit & push
    if _git_available():
        push = input("\nCommit workflow file and push now? [Y/n]: ").strip().lower()
        if push in ("", "y", "yes"):
            _run(["git", "add", ".github/"], cwd=repo_path)
            r = _run(["git", "commit", "-m", "ci: add MkDocs GitHub Pages workflow"], cwd=repo_path)
            if r.returncode == 0:
                print(_ok("Committed docs.yml"))
                r2 = _run(["git", "push"], cwd=repo_path, capture=False)
                if r2.returncode == 0:
                    print(_ok("Pushed — workflow will deploy docs on next push to default branch"))
                else:
                    print(_warn("Push failed — run 'git push' manually"))
            else:
                print(_dim("Nothing new to commit (workflow may already be staged)"))

    print(f"\n{_ok('GitHub Pages setup complete!')}")
    owner, repo = get_github_info(repo_path)
    if owner:
        print(_dim(f"  Docs URL (after first deploy): https://{owner}.github.io/{repo}/"))
    print()


# ── Local serve interactive flow ──────────────────────────────────────────────

def flow_local_serve(repo_path: Path):
    """
    Interactive wizard for serving docs locally.
    Asks for port (suggests a free one), then foreground-runs the server.
    """
    print(f"\n{_h('=' * 60)}")
    print(_h("🖥️   LOCAL DOCS SERVER"))
    print(_h("=" * 60))

    if not ensure_mkdocs_yml(repo_path):
        print(_err("Cannot proceed without mkdocs.yml"))
        return

    backend = _choose_server_backend(repo_path)
    print(f"\n{C.CYAN}Available backend: {backend}{C.RESET}")

    suggestion = find_free_port(8000)
    raw = input(f"Port to serve on [{suggestion}]: ").strip()
    try:
        port = int(raw) if raw else suggestion
    except ValueError:
        port = suggestion

    if not _is_port_free(port):
        print(_warn(f"Port {port} is in use — finding next free port…"))
        port = find_free_port(port + 1)
        print(_ok(f"Using port {port}"))

    if backend == "mkdocs":
        _serve_mkdocs_foreground(repo_path, port)
    elif backend == "caddy":
        _serve_caddy_foreground(repo_path, port)
    else:
        _serve_http_foreground(repo_path, port)


# ── Persistent service interactive flow ───────────────────────────────────────

def flow_persistent_service(repo_path: Path):
    """
    Interactive wizard for installing a systemd --user service so docs
    stay live 24/7 on a chosen port.  User points their reverse-proxy
    (nginx, Tailscale, Caddy) at http://127.0.0.1:<port>.
    """
    print(f"\n{_h('=' * 60)}")
    print(_h("⚙️   PERSISTENT DOCS SERVICE  (systemd)"))
    print(_h("=" * 60))

    if sys.platform == "win32":
        print(_warn("systemd is not supported on Windows"))
        print(_dim("  Tip: use WSL2 or run mkdocs serve manually."))
        return

    if not _systemd_available():
        print(_warn("systemd not detected on this system"))
        print(_dim("  Is systemd running?  Try: systemctl --user status"))
        return

    if not ensure_mkdocs_yml(repo_path):
        print(_err("Cannot proceed without mkdocs.yml"))
        return

    # Show existing services
    existing = list_systemd_services()
    if existing:
        print(f"\n{C.CYAN}Existing gitship doc services:{C.RESET}")
        for svc in existing:
            status_col = C.GREEN if svc["status"] == "active" else C.YELLOW
            print(f"  • port {svc['port']:5d}  [{status_col}{svc['status']}{C.RESET}]  {svc['file'].name}")
        print()

    print("Options:")
    print("  1. Install new service on a port")
    print("  2. Remove a service")
    print("  3. Show service status")
    print("  0. Back")

    choice = input("\nChoice: ").strip()

    if choice == "1":
        backend = _choose_server_backend(repo_path)
        if backend == "http" and not (repo_path / "site").exists():
            print(_warn("site/ directory not found — mkdocs build will run first"))
            if _mkdocs_available():
                _run(["mkdocs", "build"], cwd=repo_path, capture=False)
            else:
                print(_err("mkdocs not installed; cannot build site/"))
                return

        suggestion = find_free_port(8000)
        raw = input(f"Port to serve on [{suggestion}]: ").strip()
        try:
            port = int(raw) if raw else suggestion
        except ValueError:
            port = suggestion

        install_systemd_service(repo_path, port, backend)

    elif choice == "2":
        if not existing:
            print(_dim("No services to remove"))
            return
        raw = input("Port of service to remove: ").strip()
        try:
            remove_systemd_service(int(raw))
        except ValueError:
            print(_err("Invalid port"))

    elif choice == "3":
        if not existing:
            print(_dim("No gitship docs services found"))
        for svc in existing:
            r = _run(["systemctl", "--user", "status", svc["file"].name], capture=False)

    elif choice == "0":
        return


# ── main interactive menu ─────────────────────────────────────────────────────

def main_menu(repo_path: Path):
    """
    Top-level deploy menu, called from docs.py.
    """
    while True:
        print(f"\n{_h('=' * 60)}")
        print(_h("📡  DOCS DEPLOYMENT"))
        print(_h("=" * 60))

        print("  1. Preview locally (foreground, Ctrl+C to stop)")
        print("  2. Deploy to GitHub Pages (GitHub Actions)")

        if sys.platform != "win32" and _systemd_available():
            print("  3. Run as persistent service (systemd, custom port)")
        else:
            print(_dim("  3. Persistent service — not available (no systemd)"))

        print("  0. Back")

        try:
            choice = input("\nChoice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "0":
            return
        elif choice == "1":
            flow_local_serve(repo_path)
        elif choice == "2":
            flow_github_pages(repo_path)
        elif choice == "3":
            flow_persistent_service(repo_path)


# ── standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    main_menu(Path.cwd())
