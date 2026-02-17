#!/usr/bin/env python3
"""
licenses - Fetch and manage license files for project dependencies.

Automatically detects dependencies from pyproject.toml and downloads
their license files from PyPI.
"""
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import List, Optional, Set, Tuple


def read_dependencies_from_toml(repo_path: Path, include_optional: bool = False, optional_groups: Optional[List[str]] = None) -> Set[str]:
    """
    Read dependencies from pyproject.toml.
    
    Args:
        repo_path: Path to repository
        include_optional: Include all optional dependencies (deprecated - use optional_groups)
        optional_groups: List of specific optional dependency groups to include (e.g., ['dev', 'full'])
    
    Returns:
        Set of package names
    """
    pyproject_path = repo_path / "pyproject.toml"
    
    if not pyproject_path.exists():
        return set()
    
    try:
        # Try tomli for Python 3.10 and earlier
        try:
            import tomli
            with open(pyproject_path, 'rb') as f:
                data = tomli.load(f)
        except ImportError:
            # Python 3.11+ has tomllib in stdlib
            import tomllib
            with open(pyproject_path, 'rb') as f:
                data = tomllib.load(f)
        
        dependencies = set()
        
        # Get main dependencies
        if 'project' in data and 'dependencies' in data['project']:
            for dep in data['project']['dependencies']:
                # Extract package name (before any version specifier)
                pkg_name = dep.split('[')[0].split('=')[0].split('<')[0].split('>')[0].split('!')[0].split('~')[0].split(';')[0].strip()
                if pkg_name:
                    dependencies.add(pkg_name)
        
        # Get optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            opt_deps = data['project']['optional-dependencies']
            
            if optional_groups:
                # Include only specified groups
                for group in optional_groups:
                    if group in opt_deps:
                        for dep in opt_deps[group]:
                            pkg_name = dep.split('[')[0].split('=')[0].split('<')[0].split('>')[0].split('!')[0].split('~')[0].split(';')[0].strip()
                            if pkg_name:
                                dependencies.add(pkg_name)
            elif include_optional:
                # Include all optional dependencies
                for group, deps in opt_deps.items():
                    for dep in deps:
                        pkg_name = dep.split('[')[0].split('=')[0].split('<')[0].split('>')[0].split('!')[0].split('~')[0].split(';')[0].strip()
                        if pkg_name:
                            dependencies.add(pkg_name)
        
        return dependencies
        
    except Exception as e:
        print(f"⚠️  Error reading pyproject.toml: {e}")
        return set()


def get_package_urls(package_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Get download URLs from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read())
            urls = data.get('urls', [])
            
            # Prefer source distributions (more likely to have LICENSE)
            for item in urls:
                if item.get('packagetype') == 'sdist':
                    return item['url'], item['filename']
            
            # Fallback to wheel
            if urls:
                return urls[0]['url'], urls[0]['filename']
                
    except Exception as e:
        print(f"  ⚠️  Error fetching {package_name} from PyPI: {e}")
    
    return None, None


def extract_license(archive_path: str, filename: str) -> Optional[str]:
    """Extract LICENSE file from archive."""
    try:
        if filename.endswith(('.tar.gz', '.tgz')):
            with tarfile.open(archive_path, 'r:gz') as tar:
                for member in tar.getmembers():
                    name_upper = member.name.upper()
                    if 'LICENSE' in name_upper or 'COPYING' in name_upper or 'LICENCE' in name_upper:
                        f = tar.extractfile(member)
                        if f:
                            return f.read().decode('utf-8', errors='ignore')
        
        elif filename.endswith(('.zip', '.whl')):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                for name in zip_ref.namelist():
                    name_upper = name.upper()
                    if 'LICENSE' in name_upper or 'COPYING' in name_upper or 'LICENCE' in name_upper:
                        return zip_ref.read(name).decode('utf-8', errors='ignore')
                        
    except Exception as e:
        print(f"  ⚠️  Error extracting from {filename}: {e}")
    
    return None


def check_deps_status(repo_path: Path) -> Tuple[bool, str]:
    """
    Check if deps module has been run and pyproject.toml is up-to-date.
    
    Returns:
        Tuple of (is_updated, message)
    """
    try:
        from gitship.deps import find_project_imports, get_pyproject_dependencies
        
        # Get imports from source code
        imports = find_project_imports(repo_path, silent=True)
        if not imports:
            return True, "No imports found in source code"
        
        # Get current dependencies in pyproject.toml
        pyproject_deps = get_pyproject_dependencies(repo_path)
        
        # Check if all imports are covered
        missing = []
        for module in imports:
            if module not in pyproject_deps['main'] and module not in pyproject_deps['optional'] and module not in pyproject_deps['dev']:
                missing.append(module)
        
        if missing:
            return False, f"⚠️  {len(missing)} missing dependencies detected. Run 'gitship deps' first."
        
        return True, "✅ Dependencies are up-to-date"
        
    except Exception as e:
        # If deps module is not available or fails, assume it's okay
        return True, f"Could not verify (deps check unavailable: {e})"


def get_optional_groups_from_toml(repo_path: Path) -> List[str]:
    """Get list of optional dependency groups from pyproject.toml."""
    pyproject_path = repo_path / "pyproject.toml"
    
    if not pyproject_path.exists():
        return []
    
    try:
        try:
            import tomli
            with open(pyproject_path, 'rb') as f:
                data = tomli.load(f)
        except ImportError:
            import tomllib
            with open(pyproject_path, 'rb') as f:
                data = tomllib.load(f)
        
        if 'project' in data and 'optional-dependencies' in data['project']:
            return list(data['project']['optional-dependencies'].keys())
        
        return []
        
    except Exception:
        return []


def fetch_license_for_package(package_name: str, output_path: Path) -> bool:
    """Fetch and save license for a single package."""
    
    # Check if already exists
    if output_path.exists() and output_path.stat().st_size > 100:
        return True
    
    print(f"  📦 Fetching {package_name}...")
    
    # Get download URL
    url, filename = get_package_urls(package_name)
    if not url:
        print(f"    ❌ Package not found on PyPI")
        # Try to get license URL from PyPI metadata
        license_url = get_license_url_from_pypi(package_name)
        if license_url:
            try:
                print(f"    🔗 Found license link: {license_url}")
                with urllib.request.urlopen(license_url, timeout=10) as response:
                    license_text = response.read().decode('utf-8', errors='ignore')
                    output_path.write_text(license_text, encoding='utf-8')
                    print(f"    ✅ Saved from GitHub/source")
                    return True
            except Exception as e:
                print(f"    ⚠️  Failed to fetch from link: {e}")
        
        # Create placeholder with link
        create_license_placeholder(package_name, output_path, license_url)
        return False
    
    # Download to temp file
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            print(f"    ⬇️  Downloading {filename}...")
            urllib.request.urlretrieve(url, tmp.name)
            
            # Extract license
            license_text = extract_license(tmp.name, filename)
            
            if license_text:
                output_path.write_text(license_text, encoding='utf-8')
                print(f"    ✅ Saved to {output_path.name}")
                return True
            else:
                # Try to get license URL from PyPI metadata as fallback
                license_url = get_license_url_from_pypi(package_name)
                create_license_placeholder(package_name, output_path, license_url)
                print(f"    ⚠️  No license found, created placeholder")
                return False
                
    except Exception as e:
        print(f"    ❌ Error: {e}")
        return False
    finally:
        # Cleanup temp file
        try:
            if 'tmp' in locals():
                os.unlink(tmp.name)
        except:
            pass


def get_license_url_from_pypi(package_name: str) -> Optional[str]:
    """Get license URL from PyPI package metadata."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read())
            info = data.get('info', {})
            
            # Try project URLs first
            project_urls = info.get('project_urls', {})
            for key in ['License', 'license', 'LICENSE']:
                if key in project_urls:
                    return project_urls[key]
            
            # Try home page or source code
            home_page = info.get('home_page') or info.get('package_url')
            if home_page and 'github.com' in home_page:
                # Construct likely license URL for GitHub repos
                repo_url = home_page.rstrip('/')
                return f"{repo_url}/blob/main/LICENSE"
            
            return home_page
                
    except Exception:
        return None


def create_license_placeholder(package_name: str, output_path: Path, license_url: Optional[str] = None):
    """Create a placeholder license file with links."""
    placeholder = f"""License for {package_name}
=====================================

License file not found in package distribution.

"""
    
    if license_url:
        placeholder += f"""License information may be available at:
{license_url}

"""
    
    placeholder += f"""Package information:
https://pypi.org/project/{package_name}/

To view package metadata:
pip show {package_name}
"""
    
    output_path.write_text(placeholder, encoding='utf-8')


def fetch_all_licenses(repo_path: Path, interactive: bool = True, include_optional: bool = False, 
                       include_transitive: bool = False, optional_groups: Optional[List[str]] = None) -> Tuple[int, int]:
    """
    Fetch licenses for all dependencies in the project.
    
    Args:
        repo_path: Path to repository
        interactive: Ask for confirmation before fetching
        include_optional: Include all optional dependencies (deprecated - use optional_groups)
        include_transitive: Include transitive dependencies from installed packages
        optional_groups: List of specific optional dependency groups to include (e.g., ['dev', 'full'])
    
    Returns:
        Tuple of (successful, failed) counts
    """
    
    # Read dependencies
    print("📖 Reading dependencies from pyproject.toml...")
    dependencies = read_dependencies_from_toml(repo_path, include_optional=include_optional, optional_groups=optional_groups)
    
    if not dependencies:
        print("❌ No dependencies found in pyproject.toml")
        print("💡 Run 'gitship deps' first to scan and add dependencies")
        return 0, 0
    
    # Get transitive dependencies if requested
    if include_transitive:
        print("🔍 Scanning for transitive dependencies...")
        transitive = get_transitive_dependencies(repo_path)
        if transitive:
            print(f"   Found {len(transitive)} additional transitive dependencies")
            dependencies.update(transitive)
    
    print(f"✅ Found {len(dependencies)} total dependencies")
    
    # Create licenses directory
    licenses_dir = repo_path / "licenses"
    licenses_dir.mkdir(exist_ok=True)
    print(f"📁 License files will be saved to: {licenses_dir}")
    
    # Confirm if interactive
    if interactive:
        print(f"\nWill fetch licenses for:")
        for dep in sorted(dependencies):
            print(f"  • {dep}")
        
        try:
            confirm = input("\nProceed? (y/n): ").strip().lower()
            if confirm not in ('y', 'yes'):
                print("Cancelled")
                return 0, 0
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled")
            return 0, 0
    
    print()
    
    # Fetch licenses
    successful = 0
    failed = 0
    
    for package in sorted(dependencies):
        output_path = licenses_dir / f"{package}.txt"
        
        if fetch_license_for_package(package, output_path):
            successful += 1
        else:
            failed += 1
    
    print()
    print("=" * 60)
    print(f"✅ Successfully fetched: {successful}")
    if failed > 0:
        print(f"⚠️  Failed or placeholder: {failed}")
    print(f"📁 Licenses saved to: {licenses_dir}")
    print("=" * 60)
    
    return successful, failed


def get_transitive_dependencies(repo_path: Path) -> Set[str]:
    """Get transitive dependencies from requirements files or recursive resolution."""
    import subprocess
    
    # Method 1: Check if requirements.txt or requirements-trace.txt exists
    for req_file in ['requirements-trace.txt', 'requirements.txt']:
        req_path = repo_path / req_file
        if req_path.exists():
            print(f"  📄 Reading from {req_file}...")
            deps = set()
            try:
                content = req_path.read_text()
                for line in content.split('\n'):
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    # Parse package name (format: package==version or package>=version)
                    if '==' in line or '>=' in line or '<=' in line or '>' in line or '<' in line:
                        # Split on first occurrence of comparison operator
                        pkg = line.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
                        # Remove any inline comments and conditions
                        pkg = pkg.split('#')[0].split(';')[0].strip()
                        if pkg:
                            deps.add(pkg)
                if deps:
                    print(f"    Found {len(deps)} packages in {req_file}")
                    return deps
            except Exception as e:
                print(f"    ⚠️  Error reading {req_file}: {e}")
    
    # Method 2: Recursive pip show (walks dependency tree from pyproject.toml)
    print("  🔄 Using recursive dependency resolution from pyproject.toml...")
    try:
        # Get direct dependencies first
        direct_deps = read_dependencies_from_toml(repo_path, include_optional=False)
        all_deps = set(direct_deps)
        checked = set()
        
        to_check = list(direct_deps)
        
        while to_check:
            pkg = to_check.pop(0)
            if pkg in checked:
                continue
            checked.add(pkg)
            
            # Get this package's dependencies
            result = subprocess.run(
                ["pip", "show", pkg],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Requires:'):
                        requires = line.split(':', 1)[1].strip()
                        if requires:
                            for dep in requires.split(','):
                                dep = dep.strip()
                                if dep and dep not in all_deps:
                                    all_deps.add(dep)
                                    to_check.append(dep)
        
        print(f"    Found {len(all_deps)} total packages (including direct)")
        return all_deps - direct_deps  # Return only transitive ones
        
    except Exception as e:
        print(f"    ⚠️  Error in recursive resolution: {e}")
        return set()


def detect_license_type(license_text: str) -> str:
    """Detect license type from license text."""
    text_upper = license_text.upper()
    
    if 'MIT LICENSE' in text_upper or 'MIT/X CONSORTIUM LICENSE' in text_upper:
        return 'MIT'
    elif 'APACHE LICENSE' in text_upper and 'VERSION 2.0' in text_upper:
        return 'Apache-2.0'
    elif 'BSD 3-CLAUSE' in text_upper or ('BSD' in text_upper and 'THREE CLAUSE' in text_upper):
        return 'BSD-3-Clause'
    elif 'BSD 2-CLAUSE' in text_upper or ('BSD' in text_upper and 'TWO CLAUSE' in text_upper):
        return 'BSD-2-Clause'
    elif 'GNU GENERAL PUBLIC LICENSE' in text_upper:
        if 'VERSION 3' in text_upper:
            return 'GPL-3.0'
        elif 'VERSION 2' in text_upper:
            return 'GPL-2.0'
    elif 'GNU LESSER GENERAL PUBLIC LICENSE' in text_upper:
        if 'VERSION 3' in text_upper:
            return 'LGPL-3.0'
        elif 'VERSION 2' in text_upper:
            return 'LGPL-2.1'
    elif 'MOZILLA PUBLIC LICENSE' in text_upper:
        return 'MPL-2.0'
    elif 'PYTHON SOFTWARE FOUNDATION' in text_upper:
        return 'PSF'
    elif 'ISC LICENSE' in text_upper:
        return 'ISC'
    
    return 'Unknown'


def generate_third_party_notices(repo_path: Path):
    """Generate THIRD_PARTY_NOTICES.txt file with versions."""
    import subprocess
    
    licenses_dir = repo_path / "licenses"
    
    if not licenses_dir.exists():
        print("❌ No licenses directory found")
        print("💡 Run 'gitship licenses --fetch' first")
        return
    
    license_files = sorted(licenses_dir.glob("*.txt"))
    
    if not license_files:
        print("❌ No license files found")
        return
    
    print(f"📝 Generating THIRD_PARTY_NOTICES.txt...")
    
    # Read project name from pyproject.toml
    try:
        try:
            import tomli
            with open(repo_path / "pyproject.toml", 'rb') as f:
                data = tomli.load(f)
        except ImportError:
            import tomllib
            with open(repo_path / "pyproject.toml", 'rb') as f:
                data = tomllib.load(f)
        
        project_name = data.get('project', {}).get('name', 'this project')
    except:
        project_name = 'this project'
    
    # Build notices file
    notices = []
    notices.append(f"{project_name} includes the following third-party software:\n")
    notices.append("=" * 80 + "\n\n")
    
    for license_file in license_files:
        pkg_name = license_file.stem
        
        # Try to get installed version
        version = None
        try:
            result = subprocess.run(
                ["pip", "show", pkg_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Version:'):
                        version = line.split(':', 1)[1].strip()
                        break
        except:
            pass
        
        # Read license to detect type
        try:
            license_text = license_file.read_text(encoding='utf-8', errors='ignore')
            license_type = detect_license_type(license_text)
        except:
            license_type = "Unknown"
        
        # Format entry
        if version:
            notices.append(f"{pkg_name} (v{version})\n")
        else:
            notices.append(f"{pkg_name}\n")
        
        notices.append(f"License: {license_type}\n")
        notices.append(f"See licenses/{license_file.name} for full license text\n\n")
    
    # Write notices file
    notices_path = repo_path / "THIRD_PARTY_NOTICES.txt"
    notices_path.write_text(''.join(notices), encoding='utf-8')
    
    print(f"✅ Generated {notices_path.name} ({len(license_files)} packages)")
    print(f"   File size: {notices_path.stat().st_size:,} bytes")
    
    # Automatically update pyproject.toml
    print("\n📝 Updating pyproject.toml with license files...")
    update_pyproject_license_files(repo_path)


def update_pyproject_license_files(repo_path: Path):
    """Update [tool.setuptools] license-files in pyproject.toml."""
    pyproject_path = repo_path / "pyproject.toml"
    
    if not pyproject_path.exists():
        print("⚠️  No pyproject.toml found")
        return False
    
    try:
        # Read current content
        content = pyproject_path.read_text(encoding='utf-8')
        
        # Parse TOML
        try:
            import tomli
            with open(pyproject_path, 'rb') as f:
                data = tomli.load(f)
        except ImportError:
            import tomllib
            with open(pyproject_path, 'rb') as f:
                data = tomllib.load(f)
        
        # Check setuptools version requirement
        build_system = data.get('build-system', {})
        requires = build_system.get('requires', [])
        
        # Parse setuptools version if present
        setuptools_version = None
        for req in requires:
            if 'setuptools' in req.lower():
                # Extract version number if present
                import re
                match = re.search(r'setuptools[><=!~]+(\d+)', req)
                if match:
                    setuptools_version = int(match.group(1))
                break
        
        # If setuptools < 61.0, offer upgrade
        if setuptools_version and setuptools_version < 61:
            print(f"\n⚠️  Current setuptools requirement: {setuptools_version}")
            print("   Modern license file management requires setuptools >= 61.0")
            print("   This enables better license tracking in pyproject.toml")
            
            try:
                upgrade = input("\nUpgrade setuptools requirement to >=61.0? (y/n): ").strip().lower()
                if upgrade in ('y', 'yes'):
                    # Update setuptools requirement
                    new_requires = []
                    for req in requires:
                        if 'setuptools' in req.lower():
                            new_requires.append('"setuptools>=61.0"')
                        else:
                            new_requires.append(f'"{req}"' if '"' not in req else req)
                    
                    requires_str = ', '.join(new_requires)
                    
                    # Find and replace in content
                    if 'requires = [' in content:
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            if line.strip().startswith('requires = ['):
                                # Handle multi-line requires
                                if ']' not in line:
                                    # Multi-line, find end
                                    j = i + 1
                                    while j < len(lines) and ']' not in lines[j]:
                                        j += 1
                                    # Replace entire block
                                    lines[i] = f'requires = [{requires_str}]'
                                    # Remove old lines
                                    for k in range(i + 1, j + 1):
                                        lines[k] = ''
                                else:
                                    lines[i] = f'requires = [{requires_str}]'
                                break
                        content = '\n'.join(lines)
                        pyproject_path.write_text(content, encoding='utf-8')
                        print("✅ Updated setuptools requirement to >=61.0")
                        setuptools_version = 61
                else:
                    print("   Keeping current setuptools version")
                    return False
            except (KeyboardInterrupt, EOFError):
                print("\n   Keeping current setuptools version")
                return False
        
        # Collect license files that exist
        license_files_to_add = []
        if (repo_path / "LICENSE").exists():
            license_files_to_add.append("LICENSE")
        if (repo_path / "THIRD_PARTY_NOTICES.txt").exists():
            license_files_to_add.append("THIRD_PARTY_NOTICES.txt")
        
        if not license_files_to_add:
            print("ℹ️  No license files to add to pyproject.toml")
            return False
        
        # Check if [tool.setuptools] exists
        if '[tool.setuptools]' not in content:
            # Add new section
            print("\n📝 Adding [tool.setuptools] section to pyproject.toml")
            
            # Find insertion point (before [tool.black], [tool.ruff], etc or at end)
            insert_pos = content.find('\n[tool.')
            if insert_pos == -1:
                insert_pos = len(content)
            
            files_str = ', '.join(f'"{f}"' for f in license_files_to_add)
            new_section = f'\n[tool.setuptools]\nlicense-files = [{files_str}]\n'
            
            content = content[:insert_pos] + new_section + content[insert_pos:]
            pyproject_path.write_text(content, encoding='utf-8')
            print(f"✅ Added license-files: {', '.join(license_files_to_add)}")
            return True
        
        else:
            # Check if license-files already exists
            if 'license-files' in content:
                # Parse existing license-files
                import re
                match = re.search(r'license-files\s*=\s*\[(.*?)\]', content, re.DOTALL)
                if match:
                    existing = match.group(1)
                    # Parse existing files
                    existing_files = [f.strip().strip('"\'') for f in existing.split(',') if f.strip()]
                    
                    # Add missing files
                    new_files = [f for f in license_files_to_add if f not in existing_files]
                    
                    if new_files:
                        all_files = existing_files + new_files
                        files_str = ', '.join(f'"{f}"' for f in all_files)
                        new_line = f'license-files = [{files_str}]'
                        
                        content = content.replace(match.group(0), new_line)
                        pyproject_path.write_text(content, encoding='utf-8')
                        print(f"✅ Updated license-files, added: {', '.join(new_files)}")
                        return True
                    else:
                        print("ℹ️  All license files already in pyproject.toml")
                        return False
            else:
                # Add license-files to existing [tool.setuptools]
                print("\n📝 Adding license-files to existing [tool.setuptools]")
                
                # Find the [tool.setuptools] section
                setuptools_pos = content.find('[tool.setuptools]')
                if setuptools_pos != -1:
                    # Find next section or end
                    next_section_pos = content.find('\n[', setuptools_pos + 1)
                    if next_section_pos == -1:
                        next_section_pos = len(content)
                    
                    # Insert license-files after [tool.setuptools]
                    insert_pos = setuptools_pos + len('[tool.setuptools]')
                    files_str = ', '.join(f'"{f}"' for f in license_files_to_add)
                    new_line = f'\nlicense-files = [{files_str}]'
                    
                    content = content[:insert_pos] + new_line + content[insert_pos:]
                    pyproject_path.write_text(content, encoding='utf-8')
                    print(f"✅ Added license-files: {', '.join(license_files_to_add)}")
                    return True
        
        return False
        
    except Exception as e:
        print(f"⚠️  Error updating pyproject.toml: {e}")
        return False


def generate_manifest(repo_path: Path):
    """Generate MANIFEST.in file."""
    print("📝 Generating MANIFEST.in...")
    
    manifest_lines = [
        "# MANIFEST.in - Source distribution includes\n",
        "\n",
        "# Core documentation and license files\n",
        "include README.md\n",
        "include LICENSE\n",
    ]
    
    # Check for optional files
    if (repo_path / "CHANGELOG.md").exists():
        manifest_lines.append("include CHANGELOG.md\n")
    
    if (repo_path / "THIRD_PARTY_NOTICES.txt").exists():
        manifest_lines.append("include THIRD_PARTY_NOTICES.txt\n")
    
    if (repo_path / "CONTRIBUTING.md").exists():
        manifest_lines.append("include CONTRIBUTING.md\n")
    
    # Third-party licenses
    if (repo_path / "licenses").exists():
        manifest_lines.append("\n# Third-party license files\n")
        manifest_lines.append("recursive-include licenses *.txt\n")
    
    # Common excludes
    manifest_lines.append("\n# Exclude development/build artifacts\n")
    manifest_lines.append("recursive-exclude * __pycache__\n")
    manifest_lines.append("recursive-exclude * *.py[co]\n")
    
    # Write manifest
    manifest_path = repo_path / "MANIFEST.in"
    manifest_path.write_text(''.join(manifest_lines), encoding='utf-8')
    
    print(f"✅ Generated {manifest_path.name}")
    
    # Also suggest updating pyproject.toml
    print("\n💡 For modern setuptools (>=61.0), consider also updating pyproject.toml")
    print("   Use option 11 to update [tool.setuptools] license-files")


def generate_project_license(repo_path: Path, license_type: str = 'MIT'):
    """Generate LICENSE file for the project itself."""
    print(f"📝 Generating {license_type} LICENSE...")
    
    # Read author from pyproject.toml
    try:
        try:
            import tomli
            with open(repo_path / "pyproject.toml", 'rb') as f:
                data = tomli.load(f)
        except ImportError:
            import tomllib
            with open(repo_path / "pyproject.toml", 'rb') as f:
                data = tomllib.load(f)
        
        authors = data.get('project', {}).get('authors', [])
        if authors:
            author_name = authors[0].get('name', 'Your Name')
            author_email = authors[0].get('email', '')
        else:
            author_name = 'Your Name'
            author_email = ''
        
        project_name = data.get('project', {}).get('name', 'this software')
    except:
        author_name = 'Your Name'
        author_email = ''
        project_name = 'this software'
    
    import datetime
    year = datetime.datetime.now().year
    
    if license_type.upper() == 'MIT':
        license_text = f"""MIT License

Copyright (c) {year} {author_name}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
    
    elif license_type.upper() in ('AGPL-3.0', 'AGPL'):
        contact_email = author_email if author_email else f"{project_name.lower()}@example.com"
        
        license_text = f"""Copyright (c) {year} {author_name}

This file is part of `{project_name}`.

{project_name} is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

{project_name} is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with {project_name}. If not, see <https://www.gnu.org/licenses/>.

---

For commercial licensing options or general inquiries, contact:
📧  {contact_email}

---

Full license text: https://www.gnu.org/licenses/agpl-3.0.txt
"""
    
    elif license_type.upper() in ('BSD-3-CLAUSE', 'BSD-3', 'BSD'):
        license_text = f"""BSD 3-Clause License

Copyright (c) {year}, {author_name}

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
    
    elif license_type.upper() in ('APACHE-2.0', 'APACHE'):
        license_text = f"""Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

Copyright {year} {author_name}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Full license text: https://www.apache.org/licenses/LICENSE-2.0.txt
"""
    else:
        print(f"⚠️  License type {license_type} not yet supported")
        print("   Supported: MIT, AGPL-3.0, BSD-3-Clause, Apache-2.0")
        print("   Please create LICENSE file manually or choose a supported type")
        return
    
    license_path = repo_path / "LICENSE"
    if license_path.exists():
        backup = license_path.with_suffix('.LICENSE.bak')
        license_path.rename(backup)
        print(f"📦 Backed up existing LICENSE to {backup.name}")
    
    license_path.write_text(license_text, encoding='utf-8')
    print(f"✅ Generated LICENSE file")
    
    # Automatically update pyproject.toml
    print("\n📝 Updating pyproject.toml with license files...")
    update_pyproject_license_files(repo_path)



def generate_requirements_txt(repo_path: Path):
    """Generate requirements.txt using pip-compile."""
    import subprocess
    
    print("📝 Generating requirements.txt...")
    
    # Check if pip-compile is available
    try:
        subprocess.run(["pip-compile", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ pip-compile not found")
        print("💡 Install it with: pip install pip-tools")
        return
    
    # Run pip-compile
    try:
        print("   Running pip-compile (this may take a moment)...")
        result = subprocess.run(
            ["pip-compile", "--output-file=requirements.txt", "pyproject.toml"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            req_file = repo_path / "requirements.txt"
            print(f"✅ Generated requirements.txt ({req_file.stat().st_size:,} bytes)")
            
            # Count packages
            lines = req_file.read_text().split('\n')
            pkg_count = sum(1 for line in lines if line and not line.startswith('#') and '==' in line)
            print(f"   {pkg_count} packages pinned")
        else:
            print(f"❌ pip-compile failed:")
            print(result.stderr)
            
    except subprocess.TimeoutExpired:
        print("❌ pip-compile timed out (>2 minutes)")
    except Exception as e:
        print(f"❌ Error: {e}")


def list_licenses(repo_path: Path):
    """List all license files in the project."""
    licenses_dir = repo_path / "licenses"
    
    if not licenses_dir.exists():
        print("❌ No licenses directory found")
        print("💡 Run 'gitship licenses --fetch' to download licenses")
        return
    
    license_files = sorted(licenses_dir.glob("*.txt"))
    
    if not license_files:
        print("❌ No license files found")
        return
    
    print(f"\n📄 License files ({len(license_files)}):")
    print("=" * 60)
    
    for license_file in license_files:
        size = license_file.stat().st_size
        pkg_name = license_file.stem
        
        # Check if it's a placeholder
        content = license_file.read_text(encoding='utf-8', errors='ignore')
        is_placeholder = "License file not found" in content
        
        status = "⚠️  placeholder" if is_placeholder else "✅"
        print(f"  {status} {pkg_name:30s} ({size:>6,} bytes)")
    
    print()


def main_with_args(repo_path: Path, fetch: bool = False, list_files: bool = False):
    """Entry point for licenses command."""
    
    if fetch:
        fetch_all_licenses(repo_path, interactive=True)
    elif list_files:
        list_licenses(repo_path)
    else:
        # Interactive menu
        interactive_licenses(repo_path)


def interactive_licenses(repo_path: Path):
    """Interactive license management menu."""
    
    while True:
        print("\n" + "=" * 60)
        print("LICENSE MANAGER")
        print("=" * 60)
        
        # Check deps status
        deps_updated, deps_msg = check_deps_status(repo_path)
        if not deps_updated:
            print(f"\n{deps_msg}")
        
        # Show current status
        licenses_dir = repo_path / "licenses"
        if licenses_dir.exists():
            license_files = list(licenses_dir.glob("*.txt"))
            print(f"📁 License files: {len(license_files)}")
        else:
            print(f"📁 License files: 0 (directory not created)")
        
        dependencies = read_dependencies_from_toml(repo_path, include_optional=False)
        print(f"📦 Dependencies in pyproject.toml: {len(dependencies)}")
        
        # Show optional groups if they exist
        optional_groups = get_optional_groups_from_toml(repo_path)
        if optional_groups:
            print(f"📋 Optional groups available: {', '.join(optional_groups)}")
        
        # Check for existing files
        has_notices = (repo_path / "THIRD_PARTY_NOTICES.txt").exists()
        has_manifest = (repo_path / "MANIFEST.in").exists()
        has_license = (repo_path / "LICENSE").exists()
        has_requirements = (repo_path / "requirements.txt").exists()
        
        print("\nOptions:")
        print("  1. Fetch all licenses (direct deps only)")
        print("  2. Fetch all licenses (include transitive)")
        print("  3. Fetch licenses with optional groups (choose which)")
        print("  4. Generate THIRD_PARTY_NOTICES.txt" + (" (update)" if has_notices else ""))
        print("  5. Generate MANIFEST.in" + (" (update)" if has_manifest else ""))
        print("  6. Generate LICENSE file" + (" (exists)" if has_license else ""))
        print("  7. List current licenses")
        print("  8. Fetch specific package")
        print("  9. Add licenses/ to .gitignore")
        print(" 10. Generate requirements.txt" + (" (update)" if has_requirements else " (pip-compile)"))
        print(" 11. Update pyproject.toml [tool.setuptools] license-files")
        print("  0. Exit")
        
        try:
            choice = input("\nChoice (0-11): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return
        
        if choice == '0':
            return
        
        elif choice == '1':
            # Fetch direct dependencies only
            fetch_all_licenses(repo_path, interactive=True, include_optional=False, include_transitive=False)
            input("\nPress Enter to continue...")
        
        elif choice == '2':
            # Fetch including transitive
            print("\n⚠️  This will fetch licenses for ALL dependencies including transitive ones")
            print("   (This can be 50+ packages for complex projects)")
            fetch_all_licenses(repo_path, interactive=True, include_optional=False, include_transitive=True)
            input("\nPress Enter to continue...")
        
        elif choice == '3':
            # Select optional groups
            if not optional_groups:
                print("\n⚠️  No optional dependency groups found in pyproject.toml")
                input("\nPress Enter to continue...")
                continue
            
            print(f"\nAvailable optional groups:")
            for i, group in enumerate(optional_groups, 1):
                deps_in_group = read_dependencies_from_toml(repo_path, optional_groups=[group])
                print(f"  {i}. {group} ({len(deps_in_group)} packages)")
            
            print("\nEnter numbers to include (e.g., '1 2' or 'all'):")
            selection = input("> ").strip().lower()
            
            if selection == 'all':
                selected_groups = optional_groups
            else:
                try:
                    indices = [int(x) - 1 for x in selection.split()]
                    selected_groups = [optional_groups[i] for i in indices if 0 <= i < len(optional_groups)]
                except (ValueError, IndexError):
                    print("❌ Invalid selection")
                    input("\nPress Enter to continue...")
                    continue
            
            if selected_groups:
                print(f"\n✅ Selected groups: {', '.join(selected_groups)}")
                fetch_all_licenses(repo_path, interactive=True, optional_groups=selected_groups, include_transitive=False)
            else:
                print("❌ No groups selected")
            
            input("\nPress Enter to continue...")
        
        elif choice == '4':
            generate_third_party_notices(repo_path)
            input("\nPress Enter to continue...")
        
        elif choice == '5':
            generate_manifest(repo_path)
            input("\nPress Enter to continue...")
        
        elif choice == '6':
            if has_license:
                confirm = input("LICENSE already exists. Overwrite? (y/n): ").strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
            
            print("\nLicense type:")
            print("  1. MIT (recommended for open source)")
            print("  2. AGPL-3.0 (like omnipkg)")
            print("  3. BSD-3-Clause")
            print("  4. Apache-2.0")
            license_choice = input("Choice (1-4): ").strip()
            
            license_map = {
                '1': 'MIT',
                '2': 'AGPL-3.0',
                '3': 'BSD-3-Clause',
                '4': 'Apache-2.0'
            }
            
            if license_choice in license_map:
                generate_project_license(repo_path, license_map[license_choice])
            else:
                print("Invalid choice")
            
            input("\nPress Enter to continue...")
        
        elif choice == '7':
            list_licenses(repo_path)
            input("\nPress Enter to continue...")
        
        elif choice == '8':
            package = input("Package name: ").strip()
            if package:
                licenses_dir.mkdir(exist_ok=True)
                output_path = licenses_dir / f"{package}.txt"
                fetch_license_for_package(package, output_path)
                input("\nPress Enter to continue...")
        
        elif choice == '9':
            # Add licenses/ to gitignore
            try:
                from gitship import gitignore
                gitignore.add_to_gitignore(repo_path, "licenses/", "Auto-generated license files")
            except ImportError:
                print("⚠️  gitignore module not available")
            input("\nPress Enter to continue...")
        
        elif choice == '10':
            generate_requirements_txt(repo_path)
            input("\nPress Enter to continue...")
        
        elif choice == '11':
            update_pyproject_license_files(repo_path)
            input("\nPress Enter to continue...")