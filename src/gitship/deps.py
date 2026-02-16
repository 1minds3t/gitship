#!/usr/bin/env python3
"""
deps - Dependency detection and management for gitship.
"""
import ast
import sys
import re
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple
from .pypi import read_package_name
from .config import get_ignored_dependencies, add_ignored_dependency

# Try to import omnipkg functions if available
OMNIPKG_AVAILABLE = False
try:
    omnipkg_path = Path.home() / 'omnipkg' / 'src'
    if omnipkg_path.exists():
        sys.path.insert(0, str(omnipkg_path))
        from omnipkg.commands.run import convert_module_to_package_name as omnipkg_convert
        from omnipkg.commands.run import is_stdlib_module as omnipkg_is_stdlib
        OMNIPKG_AVAILABLE = True
        print("✓ Found omnipkg - using advanced detection")
except ImportError:
    print("ℹ omnipkg not found - using built-in detection")
    OMNIPKG_AVAILABLE = False

# For Python 3.10+
try:
    from sys import stdlib_module_names as STDLIB_MODULES
except ImportError:
    STDLIB_MODULES = {"os", "sys", "re", "subprocess", "pathlib", "datetime", "collections", "tempfile", "shutil", "argparse", "ast", "glob", "time", "tomllib"}


def is_stdlib_module(module_name: str) -> bool:
    """
    Determines if a module name belongs to the Python Standard Library.
    Uses omnipkg if available, otherwise fallback logic.
    """
    if not module_name:
        return False
    
    base_name = module_name.split(".")[0]

    # Always check our hardcoded list for known build-tools/pseudo-stdlibs
    # This overrides omnipkg because sometimes we want to ignore things like pkg_resources
    # even if they are technically 3rd party (setuptools).
    COMMON_STDLIBS = {
        "os", "sys", "re", "json", "math", "random", "datetime", "subprocess",
        "pathlib", "typing", "collections", "itertools", "functools", "io",
        "pickle", "copy", "enum", "dataclasses", "abc", "contextlib", "argparse",
        "shutil", "threading", "multiprocessing", "asyncio", "socket", "ssl",
        "sqlite3", "csv", "time", "logging", "warnings", "traceback", "inspect",
        "ast", "platform", "urllib", "http", "email", "xml", "html", "unittest",
        "venv", "pydoc", "pdb", "profile", "cProfile", "timeit",
        # Build tools and common irrelevant modules
        "setuptools", "wheel", "pip", "distutils",
        # Backports that are stdlib in newer Python versions or vendor packages
        "importlib_metadata", "pkg_resources", "zipp", "typing_extensions",
    }
    
    if base_name in COMMON_STDLIBS:
        return True

    # Use omnipkg if available
    if OMNIPKG_AVAILABLE:
        return omnipkg_is_stdlib(module_name)
    
    base_name = module_name.split(".")[0]
    
    # 1. Use the definitive source in Python 3.10+
    if sys.version_info >= (3, 10):
        if base_name in sys.stdlib_module_names:
            return True
    
    # 2. Check built-in modules (sys, gc, etc.)
    if base_name in sys.builtin_module_names:
        return True
    
    # 3. Fallback hardcoded list
    COMMON_STDLIBS = {
        "os", "sys", "re", "json", "math", "random", "datetime", "subprocess",
        "pathlib", "typing", "collections", "itertools", "functools", "io",
        "pickle", "copy", "enum", "dataclasses", "abc", "contextlib", "argparse",
        "shutil", "threading", "multiprocessing", "asyncio", "socket", "ssl",
        "sqlite3", "csv", "time", "logging", "warnings", "traceback", "inspect",
        "ast", "platform", "urllib", "http", "email", "xml", "html", "unittest",
        "venv", "pydoc", "pdb", "profile", "cProfile", "timeit",
        # Backports that are stdlib in newer Python versions
        "importlib_metadata",  # Backport of importlib.metadata (stdlib in 3.8+)
        "pkg_resources",  # Part of setuptools, not a dependency
    }
    return base_name in COMMON_STDLIBS


def convert_module_to_package_name(module_name: str, error_message: str = None) -> str:
    """
    Convert a module name to its likely PyPI package name.
    Uses omnipkg if available for comprehensive mapping.
    """
    # Use omnipkg if available
    if OMNIPKG_AVAILABLE:
        return omnipkg_convert(module_name, error_message)
    
    # Fallback: small mapping
    module_to_package = {
        "yaml": "pyyaml",
        "cv2": "opencv-python",
        "PIL": "pillow",
        "sklearn": "scikit-learn",
        "bs4": "beautifulsoup4",
        "requests": "requests",
        "tomli": "tomli"
    }
    base = module_name.split('.')[0]
    return module_to_package.get(base, module_name)


def find_project_imports(repo_path: Path, silent: bool = False) -> Dict[str, Set[str]]:
    """
    Parse all Python files in the src/ directory and find non-stdlib imports.
    Returns a dictionary mapping module names to the set of files that import them.
    Excludes local project modules.
    """
    src_root = repo_path / "src"
    if not src_root.is_dir():
        return {}

    # Get package name to exclude self-imports
    package_name = read_package_name(repo_path)
    
    # Build set of LOCAL modules by checking ALL .py files recursively
    project_modules = set()
    if package_name:
        project_modules.add(package_name)
    
    for py_file in src_root.glob('**/*.py'):
        if py_file.stem != '__init__':
            project_modules.add(py_file.stem)
            
            # Also add parent directory name if it has __init__.py (subpackages)
            parent = py_file.parent
            if (parent / "__init__.py").exists():
                project_modules.add(parent.name)
    
    if not silent:
        print(f"[DEBUG] Detected local project modules: {len(project_modules)} found")
    
    imports = set()
    file_imports = {}  # Track which file imports what for debug
    
    class ImportVisitor(ast.NodeVisitor):
        def __init__(self):
            self.imports = set()
            self.in_type_checking = False
            # Note: We intentionally track imports inside try/except blocks
            # because they often represent optional dependencies that SHOULD be detected.

        def visit_If(self, node):
            # Check for "if TYPE_CHECKING:"
            is_type_check = False
            try:
                if isinstance(node.test, ast.Name) and node.test.id == 'TYPE_CHECKING':
                    is_type_check = True
            except:
                pass
            
            prev_type = self.in_type_checking
            if is_type_check:
                self.in_type_checking = True
            
            self.generic_visit(node)
            
            if is_type_check:
                self.in_type_checking = prev_type

        def visit_Import(self, node):
            if not self.in_type_checking:
                for alias in node.names:
                    self.imports.add(alias.name)

        def visit_ImportFrom(self, node):
            if not self.in_type_checking:
                if node.level == 0 and node.module:
                    self.imports.add(node.module)

    for py_file in src_root.glob('**/*.py'):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                tree = ast.parse(content, filename=py_file)
            
            visitor = ImportVisitor()
            visitor.visit(tree)
            
            if visitor.imports:
                file_imports[str(py_file)] = sorted(visitor.imports)
                imports.update(visitor.imports)
            
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"Warning: Could not parse {py_file}: {e}")
            continue

    # Debug output
    if not silent:
        print(f"\n[DEBUG] Import sources:")
        for file_path, file_imports_list in sorted(file_imports.items()):
            if file_imports_list:
                # Try to make path relative
                try:
                    rel_path = Path(file_path).relative_to(repo_path)
                except ValueError:
                    rel_path = file_path
                    
                print(f"  {rel_path}:")
                for imp in file_imports_list:
                    # Filter out standard library and local modules for the debug view to be cleaner?
                    # No, show everything so user sees what is found, then we filter.
                    print(f"    - {imp}")

    # Filter out stdlib and project's own modules
    # Filter out stdlib and project's own modules
    # Map external module -> set of files where it is used
    external_usage = {}
    
    # Invert file_imports to module -> files
    module_to_files = {}
    for file_path, mods in file_imports.items():
        for mod in mods:
            if mod not in module_to_files:
                module_to_files[mod] = set()
            module_to_files[mod].add(file_path)

    for module, files in module_to_files.items():
        top_level_module = module.split('.')[0]
        
        # Skip if empty, stdlib, or local project module
        if (top_level_module and 
            not is_stdlib_module(top_level_module) and 
            top_level_module not in project_modules):
            
            if top_level_module not in external_usage:
                external_usage[top_level_module] = set()
            external_usage[top_level_module].update(files)
    
    return external_usage


def update_pyproject_toml(repo_path: Path, new_deps: list, silent: bool = False) -> bool:
    """
    Add new dependencies to pyproject.toml's [project] dependencies array.
    Removes self-reference if present.
    Returns True if file was modified.
    """
    toml_path = repo_path / "pyproject.toml"
    if not toml_path.exists() or not new_deps:
        return False

    # Get package name to exclude it
    package_name = read_package_name(repo_path)
    
    # Try to use tomllib (Python 3.11+) or tomli
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomli as tomllib
            except ImportError:
                print("Warning: tomli not available, using fallback parsing")
                tomllib = None
    except:
        tomllib = None
    
    content = toml_path.read_text()
    
    # Parse with TOML library if available
    if tomllib:
        try:
            with open(toml_path, 'rb') as f:
                data = tomllib.load(f)
            current_deps = data.get('project', {}).get('dependencies', [])
        except Exception as e:
            print(f"Warning: Could not parse pyproject.toml with TOML parser: {e}")
            current_deps = []
    else:
        # Fallback: regex parsing
        match = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
        if not match:
            print("Warning: Could not find 'dependencies' array in pyproject.toml")
            return
        
        # Extract dependency names from the matched content
        deps_content = match.group(1)
        current_deps = []
        for line in deps_content.split('\n'):
            line = line.strip().strip(',').strip()
            if line and (line.startswith('"') or line.startswith("'")):
                current_deps.append(line.strip('"\''))
    
    # Remove self-reference from current deps
    if package_name:
        current_deps = [d for d in current_deps if re.split(r'[<>=!~\[]', d.strip())[0] != package_name]
        new_deps = [d for d in new_deps if d != package_name]
    
    # Extract base package names for comparison
    existing_deps_set = {re.split(r'[<>=!~\[]', d.strip())[0] for d in current_deps}
    
    added = False
    for dep in new_deps:
        if dep not in existing_deps_set and dep != package_name:
            current_deps.append(dep)
            added = True
    
    if added:
        # Format dependencies with proper quoting
        sorted_deps = sorted(current_deps)
        new_deps_str = "[\n    " + ",\n    ".join(f'"{d}"' for d in sorted_deps) + ",\n]"
        
        # Replace the dependencies section
        match = re.search(r"dependencies\s*=\s*\[.*?\]", content, re.DOTALL)
        if match:
            new_content = content.replace(match.group(0), f"dependencies = {new_deps_str}")
            toml_path.write_text(new_content)
            actual_added = len([d for d in new_deps if d != package_name])
            if not silent:
                print(f"✅ Updated pyproject.toml with {actual_added} dependencies.")
            return True
        else:
            if not silent:
                print("Warning: Could not update pyproject.toml")
            return False
    else:
        if not silent:
            print("✓ pyproject.toml is already up to date.")
        return False

def check_and_update_deps(repo_path: Path, silent: bool = False) -> bool:
    """
    Scan and update dependencies with interactive selection.
    """
    if not silent:
        print(f"\n🔍 Scanning for project dependencies... (omnipkg: {'enabled' if OMNIPKG_AVAILABLE else 'disabled'})")
    
    # Get mapping of module -> files
    usage_map = find_project_imports(repo_path, silent=silent)
    
    if not usage_map:
        if not silent:
            print("✓ No external dependencies found.")
        return False
    
    # Map packages to their usage (merging modules that map to same package)
    pkg_usage = {}
    for mod, files in usage_map.items():
        pkg = convert_module_to_package_name(mod)
        if pkg not in pkg_usage:
            pkg_usage[pkg] = set()
        pkg_usage[pkg].update(files)
    
    # Filter out self-reference
    package_name = read_package_name(repo_path)
    if package_name and package_name in pkg_usage:
        del pkg_usage[package_name]

    # Check existing
    toml_path = repo_path / "pyproject.toml"
    existing_deps_set = set()
    if toml_path.exists():
        # (Simplified existing dep extraction for brevity - logic preserved)
        try:
            content = toml_path.read_text()
            # Regex fallback for speed/simplicity
            match = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
            if match:
                for line in match.group(1).split('\n'):
                    d = line.strip().strip(',"\'')
                    if d: existing_deps_set.add(re.split(r'[<>=!~\[]', d)[0].lower().replace('_', '-'))
            
            # Check optional
            opt_match = re.findall(r'(\w+)\s*=\s*\[(.*?)\]', content, re.DOTALL)
            for grp, raw_deps in opt_match:
                # Naive check to ignore non-dep sections but works for typical TOML
                if 'optional-dependencies' in content:
                    for line in raw_deps.split('\n'):
                         d = line.strip().strip(',"\'')
                         if d: existing_deps_set.add(re.split(r'[<>=!~\[]', d)[0].lower().replace('_', '-'))
        except:
            pass

    # Identify new packages
    # Load ignored dependencies from config (project-specific)
    ignored_deps = set(get_ignored_dependencies(repo_path))
    if ignored_deps and not silent:
        print(f"[DEBUG] Permanently ignoring for this project: {', '.join(sorted(ignored_deps))}")

    # Identify new packages
    new_pkgs = []
    for pkg, files in pkg_usage.items():
        norm_pkg = pkg.lower().replace('_', '-')
        
        # Skip if already in toml OR in permanent ignore list
        if norm_pkg not in existing_deps_set and norm_pkg not in ignored_deps and pkg not in ignored_deps:
            # Determine default category based on file locations
            has_test_files = any('test' in str(f) for f in files)
            has_non_test_files = any('test' not in str(f) for f in files)
            
            # Priority: non-test files > test files
            # If it appears in ANY non-test file, it should be 'main' or 'optional'
            if has_non_test_files:
                default_cat = 'main'
            else:
                # Only in test files
                default_cat = 'dev'
            
            # Special case for omnipkg
            if pkg == 'omnipkg': default_cat = 'optional'
            
            new_pkgs.append({
                'name': pkg,
                'files': files,
                'category': default_cat
            })
    
    if not new_pkgs:
        if not silent:
            print("✓ Dependencies up to date.")
        return False

    while True:
        print(f"\n📦 Detected {len(new_pkgs)} new dependencies:")
        for i, item in enumerate(new_pkgs, 1):
            cat_code = item['category'][0].upper()
            count = len(item['files'])
            first_file = list(item['files'])[0]
            # Convert to Path if it's a string
            if isinstance(first_file, str):
                first_file = Path(first_file)
            file_sample = first_file.name if count == 1 else f"{count} files"
            if 'tests' in str(first_file):
                file_sample += " (tests)"
            print(f"  {i}. {item['name']} ({file_sample}) -> [{cat_code}]{item['category'][1:]}")

        print("\nOptions:")
        print("  [y]es      - Apply defaults")
        print("  [i]gnore   - Ignore all (this run only)")
        print("  [f]orever  - Ignore specific packages PERMANENTLY")
        print("  [u]nignore - Remove packages from permanent ignore list")
        print("  [e]dit     - Edit individual selections")
        
        choice = input("\nChoice [y]: ").strip().lower()
        if not choice: choice = 'y'
        
        if choice == 'i':
            return False
            
        elif choice == 'f':
            print("\nEnter numbers or names to ignore forever (e.g. '1 3' or 'torch'):")
            to_ignore = input("> ").strip().split()
            
            # Collect packages to ignore first, then remove from list
            packages_to_ignore = []
            for item in to_ignore:
                pkg_to_add = None
                # Check if it's a number
                if item.isdigit():
                    idx = int(item) - 1
                    if 0 <= idx < len(new_pkgs):
                        pkg_to_add = new_pkgs[idx]['name']
                else:
                    # Check if it matches a name in the list
                    for p in new_pkgs:
                        if p['name'] == item:
                            pkg_to_add = item
                            break
                
                if pkg_to_add:
                    packages_to_ignore.append(pkg_to_add)
            
            # Now add all to ignore list and remove from new_pkgs
            for pkg_name in packages_to_ignore:
                add_ignored_dependency(pkg_name, repo_path)
            
            # Remove all ignored packages from the list
            new_pkgs = [p for p in new_pkgs if p['name'] not in packages_to_ignore]
            
            if not new_pkgs:
                print("All pending dependencies ignored.")
                return False
            
            # Loop back to show updated list
            continue
            
        elif choice == 'u':
            # Show current ignore list
            from gitship.config import list_ignored_dependencies_for_project, remove_ignored_dependency
            
            current_ignored = get_ignored_dependencies(repo_path)
            if not current_ignored:
                print("\n⚠️  No packages are currently in the permanent ignore list for this project.")
                input("\nPress Enter to continue...")
                continue
            
            print(f"\nCurrently ignored packages for this project:")
            for i, pkg in enumerate(sorted(current_ignored), 1):
                print(f"  {i}. {pkg}")
            
            print("\nEnter numbers or names to UNIGNORE (e.g. '1 3' or 'torch numpy'):")
            print("(These will appear in future dependency checks)")
            to_unignore = input("> ").strip().split()
            
            for item in to_unignore:
                pkg_to_remove = None
                # Check if it's a number
                if item.isdigit():
                    idx = int(item) - 1
                    if 0 <= idx < len(current_ignored):
                        pkg_to_remove = sorted(current_ignored)[idx]
                else:
                    # Check if it matches a name in the ignore list
                    if item in current_ignored:
                        pkg_to_remove = item
                
                if pkg_to_remove:
                    remove_ignored_dependency(pkg_to_remove, repo_path)
            
            print("\n✓ Ignore list updated. Re-scanning dependencies...")
            # Recursively call to re-scan with updated ignore list
            return check_and_update_deps(repo_path, silent=silent)
            
        else:
            break
    
    final_actions = {'main': [], 'dev': [], 'optional': []}
    
    if choice == 'e':
        print("\nSelect category for each (Main/Dev/Optional/Ignore):")
        for item in new_pkgs:
            while True:
                cat = input(f"  {item['name']} [{item['category'][0].upper()}]: ").strip().lower()
                if not cat: cat = item['category'][0].lower()
                
                if cat.startswith('m'):
                    final_actions['main'].append(item['name'])
                    break
                elif cat.startswith('d'):
                    final_actions['dev'].append(item['name'])
                    break
                elif cat.startswith('o'):
                    final_actions['optional'].append(item['name'])
                    break
                elif cat.startswith('i'):
                    print(f"    Ignored {item['name']}")
                    break
    else:
        # Apply defaults
        for item in new_pkgs:
            if item['category'] == 'main': final_actions['main'].append(item['name'])
            elif item['category'] == 'dev': final_actions['dev'].append(item['name'])
            elif item['category'] == 'optional': final_actions['optional'].append(item['name'])

    modified = False
    
    if final_actions['main']:
        if update_pyproject_toml(repo_path, final_actions['main'], silent=silent):
            modified = True
            
    if final_actions['dev']:
        add_optional_dependencies(repo_path, final_actions['dev'], group='dev')
        modified = True
        
    if final_actions['optional']:
        add_optional_dependencies(repo_path, final_actions['optional'], group='full')
        modified = True
        
    return modified


def main_with_repo(repo_path: Path):
    """
    Main entry point for dependency check.
    """
    check_and_update_deps(repo_path, silent=False)
    
def add_optional_dependencies(repo_path: Path, optional_deps: list, group: str = "full"):
    """
    Add optional dependencies to pyproject.toml under [project.optional-dependencies].
    """
    toml_path = repo_path / "pyproject.toml"
    content = toml_path.read_text()
    
    # Check if [project.optional-dependencies] exists
    if '[project.optional-dependencies]' not in content:
        # Add it before [project.urls] or at end of [project] section
        insert_pos = content.find('[project.urls]')
        if insert_pos == -1:
            insert_pos = content.find('[tool.')
        
        if insert_pos > 0:
            deps_list = ", ".join(f'"{d}"' for d in sorted(optional_deps))
            optional_section = f'\n[project.optional-dependencies]\n{group} = [{deps_list}]\n'
            content = content[:insert_pos] + optional_section + content[insert_pos:]
            toml_path.write_text(content)
            print(f"✅ Added optional '{group}' dependencies: {', '.join(optional_deps)}")
    else:
        # Check if specific group exists
        pattern = rf'{group}\s*=\s*\[(.*?)\]'
        match = re.search(pattern, content, re.DOTALL)
        
        if match:
            # Update existing group
            current_raw = match.group(1)
            current_deps = [d.strip().strip('"\'') for d in current_raw.split(',') if d.strip()]
            
            # Add new ones
            updated_deps = sorted(list(set(current_deps + optional_deps)))
            deps_list = "[\n    " + ",\n    ".join(f'"{d}"' for d in updated_deps) + ",\n]"
            
            new_content = content.replace(match.group(0), f"{group} = {deps_list}")
            toml_path.write_text(new_content)
            print(f"✅ Updated optional '{group}' extra")
        else:
            # Add new group to existing section
            deps_list = ", ".join(f'"{d}"' for d in sorted(optional_deps))
            content = content.replace(
                '[project.optional-dependencies]',
                f'[project.optional-dependencies]\n{group} = [{deps_list}]'
            )
            toml_path.write_text(content)
            print(f"✅ Added optional '{group}' extra")