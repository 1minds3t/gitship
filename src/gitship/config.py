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
        }
    
    try:
        with open(config_file, 'r') as f:
            return json.load(f)
    except Exception:
        # Return defaults on error
        return {
            "export_path": str(get_default_export_path()),
            "auto_push": True,
            "default_commit_count": 10,
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
    print()
    print("To modify settings:")
    print("  gitship config --set-export-path /path/to/export")
    print(f"  Or edit: {get_config_file()}")
    print()