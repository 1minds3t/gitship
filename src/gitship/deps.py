#!/usr/bin/env python3
"""
deps - Dependency detection and management for gitship.
"""
import ast
import sys
import re
from pathlib import Path
from .pypi import read_package_name

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


def find_project_imports(repo_path: Path) -> set:
    """
    Parse all Python files in the src/ directory and find non-stdlib imports.
    """
    src_root = repo_path / "src"
    if not src_root.is_dir():
        return set()

    # Get package name to exclude self-imports
    package_name = read_package_name(repo_path)
    project_modules = {p.stem for p in src_root.glob('**/*.py')}
    if package_name:
        project_modules.add(package_name)
    
    imports = set()

    for py_file in src_root.glob('**/*.py'):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                tree = ast.parse(content, filename=py_file)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module)
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"Warning: Could not parse {py_file}: {e}")
            continue
    
    # Filter out stdlib and project's own modules
    external_imports = set()
    for module in imports:
        top_level_module = module.split('.')[0]
        if (top_level_module and 
            not is_stdlib_module(top_level_module) and 
            top_level_module not in project_modules):
            external_imports.add(top_level_module)
    
    return external_imports


def update_pyproject_toml(repo_path: Path, new_deps: list):
    """
    Add new dependencies to pyproject.toml's [project] dependencies array.
    Removes self-reference if present.
    """
    toml_path = repo_path / "pyproject.toml"
    if not toml_path.exists() or not new_deps:
        return

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
            print(f"✅ Updated pyproject.toml with {actual_added} dependencies.")
        else:
            print("Warning: Could not update pyproject.toml")
    else:
        print("✓ pyproject.toml is already up to date.")

def main_with_repo(repo_path: Path):
    """
    Main entry point for dependency check.
    """
    print(f"\n🔍 Scanning for project dependencies... (omnipkg: {'enabled' if OMNIPKG_AVAILABLE else 'disabled'})")
    
    if not OMNIPKG_AVAILABLE:
        print("ℹ  Install omnipkg for better dependency detection:")
        print("   pip install omnipkg")
    
    imports = find_project_imports(repo_path)
    
    if not imports:
        print("✓ No external dependencies found.")
        return
        
    print(f"-> Found potential modules: {', '.join(sorted(imports))}")
    
    packages = {convert_module_to_package_name(mod) for mod in imports}
    
    # Remove self-reference
    package_name = read_package_name(repo_path)
    if package_name and package_name in packages:
        packages.remove(package_name)
        print(f"-> Excluded self-reference: {package_name}")
    
    # Separate required vs optional dependencies
    required = []
    optional = []
    
    for pkg in packages:
        if pkg == 'omnipkg':
            optional.append(pkg)
        else:
            required.append(pkg)
    
    print(f"-> Required packages: {', '.join(sorted(required))}")
    if optional:
        print(f"-> Optional packages: {', '.join(sorted(optional))}")
    
    # Update only required dependencies
    update_pyproject_toml(repo_path, required)
    
    # Add optional dependencies section if needed
    if optional:
        add_optional_dependencies(repo_path, optional)


def add_optional_dependencies(repo_path: Path, optional_deps: list):
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
            deps_list = ", ".join(f'"{d}"' for d in optional_deps)
            optional_section = f'\n[project.optional-dependencies]\nfull = [{deps_list}]\n'
            content = content[:insert_pos] + optional_section + content[insert_pos:]
            toml_path.write_text(content)
            print(f"✅ Added optional dependencies: {', '.join(optional_deps)}")
            print("   Install with: pip install gitship[full]")
    else:
        # Update existing 'full' extra or add it
        match = re.search(r'full\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            print("ℹ  Optional dependencies already configured")
        else:
            # Add 'full' extra
            deps_list = ", ".join(f'"{d}"' for d in optional_deps)
            content = content.replace(
                '[project.optional-dependencies]',
                f'[project.optional-dependencies]\nfull = [{deps_list}]'
            )
            toml_path.write_text(content)
            print(f"✅ Added optional 'full' extra")