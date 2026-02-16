#!/usr/bin/env python3
"""
config - Configuration management for gitship.

Handles user preferences like default export paths, auto-push settings, etc.
"""

import json
from pathlib import Path
from typing import Dict, Any


def get_config_dir() -> Path:
    """Get the gitship configuration directory."""
    config_dir = Path.home() / ".gitship"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """Get the path to the configuration file."""
    return get_config_dir() / "config.json"


def get_default_export_path() -> Path:
    """Get the default export path for diff files."""
    # Default to ~/omnipkg_git_cleanup or ~/gitship_exports
    default_path = Path.home() / "omnipkg_git_cleanup"
    if not default_path.exists():
        default_path = Path.home() / "gitship_exports"
    return default_path


def load_config() -> Dict[str, Any]:
    """Load configuration from file."""
    config_file = get_config_file()
    
    if not config_file.exists():
        # Return defaults
        return {
            "export_path": str(get_default_export_path()),
            "auto_push": True,
            "default_commit_count": 10,
            "project_ignored_deps": {},  # Format: {"project_path": ["dep1", "dep2"]}
            "project_ignore_patterns": {},  # Format: {"project_path": ["*.po", "*.mo"]}
        }
    
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            # Migrate old config format if needed
            if "ignored_deps" in config and "project_ignored_deps" not in config:
                print("ℹ Migrating old global ignored_deps to project-specific format...")
                config["project_ignored_deps"] = {}
                del config["ignored_deps"]
            # Add project_ignore_patterns if missing
            if "project_ignore_patterns" not in config:
                config["project_ignore_patterns"] = {}
            return config
    except Exception:
        # Return defaults on error
        return {
            "export_path": str(get_default_export_path()),
            "auto_push": True,
            "default_commit_count": 10,
            "project_ignored_deps": {},
            "project_ignore_patterns": {},
        }


def save_config(config: Dict[str, Any]):
    """Save configuration to file."""
    config_file = get_config_file()
    
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving configuration: {e}")


def set_export_path(path: str):
    """Set the default export path."""
    config = load_config()
    config['export_path'] = path
    save_config(config)
    print(f"Export path set to: {path}")


def set_auto_push(enabled: bool):
    """Set auto-push preference."""
    config = load_config()
    config['auto_push'] = enabled
    save_config(config)
    print(f"Auto-push {'enabled' if enabled else 'disabled'}")


def add_ignored_dependency(package_name: str, project_path: Path = None):
    """Add a package to the persistent ignore list for a specific project."""
    config = load_config()
    
    # Determine project identifier (use absolute path as key)
    if project_path is None:
        project_path = Path.cwd()
    project_key = str(project_path.resolve())
    
    # Initialize project_ignored_deps if not present
    if 'project_ignored_deps' not in config:
        config['project_ignored_deps'] = {}
    
    # Get or create ignore list for this project
    if project_key not in config['project_ignored_deps']:
        config['project_ignored_deps'][project_key] = []
    
    ignored = set(config['project_ignored_deps'][project_key])
    ignored.add(package_name)
    config['project_ignored_deps'][project_key] = sorted(list(ignored))
    
    save_config(config)
    print(f"Dependency '{package_name}' added to ignore list for this project.")

def get_ignored_dependencies(project_path: Path = None) -> list:
    """Get list of ignored dependencies for a specific project."""
    config = load_config()
    
    # Determine project identifier
    if project_path is None:
        project_path = Path.cwd()
    project_key = str(project_path.resolve())
    
    # Get project-specific ignored deps
    project_ignored = config.get('project_ignored_deps', {})
    return project_ignored.get(project_key, [])


def remove_ignored_dependency(package_name: str, project_path: Path = None):
    """Remove a package from the project's ignore list."""
    config = load_config()
    
    # Determine project identifier
    if project_path is None:
        project_path = Path.cwd()
    project_key = str(project_path.resolve())
    
    # Get project-specific ignored deps
    project_ignored = config.get('project_ignored_deps', {})
    if project_key in project_ignored:
        if package_name in project_ignored[project_key]:
            project_ignored[project_key].remove(package_name)
            config['project_ignored_deps'] = project_ignored
            save_config(config)
            print(f"Dependency '{package_name}' removed from ignore list for this project.")
        else:
            print(f"Dependency '{package_name}' was not in the ignore list.")
    else:
        print(f"No ignored dependencies for this project.")


def list_ignored_dependencies_for_project(project_path: Path = None):
    """Display ignored dependencies for the current project."""
    if project_path is None:
        project_path = Path.cwd()
    
    ignored = get_ignored_dependencies(project_path)
    project_name = project_path.name
    
    print(f"\nIgnored dependencies for '{project_name}':")
    if ignored:
        for dep in sorted(ignored):
            print(f"  - {dep}")
    else:
        print("  (none)")
    print()

def show_config():
    """Display current configuration."""
    config = load_config()
    
    print("\n" + "=" * 60)
    print("GITSHIP CONFIGURATION")
    print("=" * 60)
    print(f"Config file: {get_config_file()}")
    print()
    print("Settings:")
    print(f"  Export Path:        {config.get('export_path', get_default_export_path())}")
    print(f"  Auto-push:          {config.get('auto_push', True)}")
    print(f"  Default Commits:    {config.get('default_commit_count', 10)}")
    
    # Show project-specific ignored deps
    project_ignored = config.get('project_ignored_deps', {})
    if project_ignored:
        print("\n  Project-specific ignored dependencies:")
        for project, deps in project_ignored.items():
            project_name = Path(project).name
            print(f"    {project_name}: {', '.join(deps) if deps else '(none)'}")
    else:
        print(f"  Ignored Deps:       (none)")
    
    # Show project-specific ignore patterns
    project_patterns = config.get('project_ignore_patterns', {})
    if project_patterns:
        print("\n  Project-specific ignore patterns (for atomic git ops):")
        for project, patterns in project_patterns.items():
            project_name = Path(project).name
            print(f"    {project_name}: {', '.join(patterns) if patterns else '(none)'}")
    else:
        print(f"  Ignore Patterns:    (defaults: *.po, *.mo)")
        
    print()
    print("To modify settings:")
    print("  gitship config --set-export-path /path/to/export")
    print(f"  Or edit: {get_config_file()}")
    print()