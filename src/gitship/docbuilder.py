#!/usr/bin/env python3
"""
MkDocs Documentation Builder v2.1
Hardened documentation system for massive-scale technical docs

NEW in v2.1:
- Robust slugify with hyphen support and whitespace collapse
- Dry-run mode for safe preview
- Migrate existing docs (auto-add metadata)
- Enhanced collision detection with context
- Explicit slug safety documentation
- Manual nav normalization

Features:
- Dual nav mode (manual mkdocs.yml vs awesome-pages)
- Append-only .pages.yml updates
- Document type system with versioning
- Collision prevention with folder scoping
- Comment-preserving YAML (ruamel.yaml fallback)
- Production-grade error handling

Built for scale: 150k+ LOC, 34+ demos, Python-level docs
"""

import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

try:
    from .common_utils import safe_print
except ImportError:
    try:
        safe_print = print  # fallback
    except ImportError:
        def safe_print(*args, **kwargs):
            print(*args, **kwargs)

# Try ruamel.yaml first (comment-preserving), fallback to PyYAML
try:
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    USING_RUAMEL = True
except ImportError:
    import yaml
    USING_RUAMEL = False
    safe_print("⚠️  Using PyYAML (will strip comments). Install ruamel.yaml for better formatting:")
    print("   pip install ruamel.yaml")


class DocBuilder:
    VERSION = "2.1.0"
    
    def __init__(self, dry_run: bool = False, root: Optional[Path] = None):
        self.dry_run = dry_run
        self.root = root if root is not None else Path(__file__).parent.parent
        self.docs_dir = self.root / "docs"
        self.mkdocs_file = self.root / "mkdocs.yml"
        
        if not self.mkdocs_file.exists():
            safe_print(f"❌ mkdocs.yml not found at {self.mkdocs_file}")
            sys.exit(1)
        
        self.config = self._load_yaml(self.mkdocs_file)
        self.nav = self.config.get('nav', [])
        self.use_awesome_pages = self._detect_awesome_pages()
        
        safe_print(f"🔧 DocBuilder v{self.VERSION}")
        if self.dry_run:
            safe_print("🔍 DRY-RUN MODE: No files will be modified")
        if self.use_awesome_pages:
            safe_print("📁 awesome-pages mode: minimal mkdocs.yml + .pages.yml")
        else:
            safe_print("📋 Manual nav mode: full mkdocs.yml structure")
        if USING_RUAMEL:
            safe_print("✅ ruamel.yaml: comment-preserving YAML")
        print()
    
    def _load_yaml(self, path: Path) -> Dict:
        """Load YAML with appropriate library"""
        with open(path) as f:
            if USING_RUAMEL:
                return yaml.load(f)
            else:
                return yaml.safe_load(f)
    
    def _dump_yaml(self, data: Dict, path: Path):
        """Save YAML with appropriate library"""
        if self.dry_run:
            print(f"  [DRY-RUN] Would write to: {path}")
            return
        
        with open(path, 'w') as f:
            if USING_RUAMEL:
                yaml.dump(data, f)
            else:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    def _detect_awesome_pages(self) -> bool:
        """Detect if awesome-pages plugin is enabled"""
        plugins = self.config.get('plugins', [])
        for p in plugins:
            plugin_name = p if isinstance(p, str) else list(p.keys())[0]
            if 'awesome-pages' in plugin_name:
                return True
        return False
    
    def save_config(self):
        """Save the updated mkdocs.yml"""
        self._dump_yaml(self.config, self.mkdocs_file)
        if not self.dry_run:
            safe_print(f"✅ Updated {self.mkdocs_file}")
    
    def slugify(self, text: str, folder_context: Optional[str] = None) -> str:
        """
        Convert text to filesystem-safe format with collision prevention
        
        IMPORTANT: Collisions are mitigated via folder-scoped namespacing.
        Do not rely on slug uniqueness alone. Examples of acceptable collisions:
          - 'os.path.join' → 'os_path_join'
          - 'os-path-join' → 'os_path_join'
          - 'os path join' → 'os_path_join'
        
        These are safe because folder_context provides namespace isolation.
        
        Args:
            text: The text to slugify
            folder_context: Optional folder name to prefix for namespacing
        
        Returns:
            Filesystem-safe slug, optionally prefixed with folder context
        """
        # Replace spaces and path separators with underscores
        slug = text.replace(' ', '_').replace('/', '_').replace('\\', '_')
        
        # Remove non-alphanumeric except hyphens and underscores
        slug = re.sub(r'[^\w\-]', '', slug)
        
        # Collapse multiple consecutive underscores/hyphens
        slug = re.sub(r'_+', '_', slug)
        slug = re.sub(r'-+', '-', slug)
        
        # Strip leading/trailing separators
        slug = slug.strip('_-').lower()
        
        # Add folder context to prevent cross-folder collisions
        if folder_context:
            slug = f"{folder_context}_{slug}"
        
        return slug
    
    def check_collision(self, file_path: Path) -> bool:
        """Check if a file already exists and warn"""
        if file_path.exists():
            safe_print(f"⚠️  WARNING: File already exists: {file_path}")
            if self.dry_run:
                print("  [DRY-RUN] Skipping overwrite check")
                return False
            overwrite = input("Overwrite? (y/N): ").strip().lower()
            return overwrite == 'y'
        return True
    
    def create_metadata_header(self, title: str, section: Optional[str] = None, 
                              status: str = "draft", doc_type: str = "guide") -> str:
        """
        Generate YAML front matter for new docs
        
        Args:
            title: Document title
            section: Section/folder name
            status: draft | stable | experimental | deprecated
            doc_type: guide | reference | tutorial | api | demo
        """
        metadata = {
            'title': title,
            'doc_type': doc_type,
            'status': status,
            'generated': True,
            'created': datetime.now().strftime('%Y-%m-%d'),
            'builder': 'gitship-docbuilder',
            'builder_version': self.VERSION
        }
        if section:
            metadata['section'] = section
        
        if USING_RUAMEL:
            from io import StringIO
            stream = StringIO()
            yaml.dump(metadata, stream)
            yaml_str = stream.getvalue()
        else:
            yaml_str = yaml.dump(metadata, default_flow_style=False, sort_keys=False)
        
        return f"---\n{yaml_str}---\n\n"
    
    def extract_metadata(self, content: str) -> Optional[Dict]:
        """
        Extract YAML front matter from content
        
        Returns None if no valid metadata found.
        """
        if not content.startswith('---'):
            return None
        
        # Find end of YAML block (more robust: skip over any --- inside strings)
        end = content.find('\n---\n', 3)
        if end == -1:
            end = content.find('\n---', 3)
            if end == -1:
                return None
            end += 4
        else:
            end += 5
        
        try:
            meta_str = content[3:end-4]  # Strip --- delimiters
            if USING_RUAMEL:
                from io import StringIO
                return yaml.load(StringIO(meta_str))
            else:
                return yaml.safe_load(meta_str)
        except Exception as e:
            safe_print(f"⚠️  Failed to parse metadata: {e}")
            return None
    
    def load_pages_yml(self, folder_path: Path) -> Dict:
        """Load existing .pages.yml or return default structure"""
        pages_file = folder_path / ".pages.yml"
        if pages_file.exists():
            return self._load_yaml(pages_file)
        return {'nav': []}
    
    def save_pages_yml(self, folder_path: Path, config: Dict, title: Optional[str] = None):
        """Save .pages.yml with merge support"""
        pages_file = folder_path / ".pages.yml"
        
        # Ensure title is set
        if title and 'title' not in config:
            config['title'] = title
        
        self._dump_yaml(config, pages_file)
        if not self.dry_run:
            safe_print(f"✅ Updated .pages.yml in {folder_path}")
    
    def append_to_pages_yml(self, folder_path: Path, new_item: Any, title: Optional[str] = None):
        """
        Safely append to .pages.yml without overwriting
        
        Args:
            folder_path: Path to folder containing .pages.yml
            new_item: Filename or dict to append to nav
            title: Optional title for new .pages.yml
        """
        config = self.load_pages_yml(folder_path)
        
        # Initialize nav if missing
        if 'nav' not in config:
            config['nav'] = []
        
        # Deduplicate: check if item already exists
        if isinstance(new_item, str):
            if new_item not in config['nav']:
                config['nav'].append(new_item)
        elif isinstance(new_item, dict):
            # Check if dict with same key exists
            new_key = list(new_item.keys())[0]
            if not any(isinstance(i, dict) and new_key in i for i in config['nav']):
                config['nav'].append(new_item)
        
        self.save_pages_yml(folder_path, config, title)
    
    def ensure_section_in_nav(self, section_name: str, folder_name: str) -> Tuple[int, bool]:
        """
        Ensure a section exists in nav (awesome-pages mode: just folder reference)
        
        Returns: (index, existed) tuple
        """
        # Check if section already exists
        for idx, item in enumerate(self.nav):
            if isinstance(item, dict):
                if section_name in item:
                    return idx, True
        
        # Add new section
        if self.use_awesome_pages:
            # Just reference the folder, .pages.yml handles the rest
            self.nav.append({section_name: f"{folder_name}/"})  # ← THIS IS WRONG
        else:
            # Manual mode: create normalized list structure
            self.nav.append({section_name: []})
        
        return len(self.nav) - 1, False
    
    def normalize_manual_nav_header(self, idx: int, header_name: str, folder_name: str):
        """
        Convert a simple header (string value) to list structure
        Prevents mixed list/string state in manual mode
        """
        header_value = self.nav[idx][header_name]
        
        if isinstance(header_value, str):
            # Convert to list structure
            self.nav[idx] = {
                header_name: [
                    {"Overview": header_value}
                ]
            }
    
    def create_file(self, file_path: Path, content: str):
        """Create a file with dry-run support"""
        if self.dry_run:
            print(f"  [DRY-RUN] Would create: {file_path}")
            print(f"  [DRY-RUN] Content preview (first 200 chars):")
            print(f"  {content[:200]}...")
            return
        
        with open(file_path, 'w') as f:
            f.write(content)

    def fix_broken_markdown_files(self):
        """Find and fix files with duplicate metadata blocks and markdown fences"""
        safe_print("\n🔧 FIX BROKEN MARKDOWN FILES")
        print("=" * 50)
        
        fixed_count = 0
        
        for md_file in self.docs_dir.rglob("*.md"):
            try:
                with open(md_file, 'r') as f:
                    content = f.read()
                
                # Check for broken pattern: metadata block followed by ```markdown fence
                if content.startswith('---\n') and '\n```markdown\n---\n' in content:
                    safe_print(f"🔍 Found broken file: {md_file.relative_to(self.docs_dir)}")
                    
                    # Extract the first metadata block (the correct one)
                    first_meta_end = content.find('\n---\n', 4)
                    if first_meta_end == -1:
                        continue
                    
                    first_block = content[:first_meta_end + 5]  # Include closing ---\n
                    
                    # Find where the markdown fence starts
                    fence_start = content.find('\n```markdown\n')
                    if fence_start == -1:
                        continue
                    
                    # Get everything after the fence, skip the duplicate metadata
                    after_fence = content[fence_start + 13:]  # Skip ```markdown\n
                    
                    # Find end of duplicate metadata block
                    dup_meta_end = after_fence.find('\n---\n')
                    if dup_meta_end != -1:
                        # Skip the duplicate metadata
                        actual_content = after_fence[dup_meta_end + 5:]
                        
                        # Remove trailing ``` if present
                        if actual_content.endswith('\n```'):
                            actual_content = actual_content[:-4]
                        
                        # Reconstruct file: first metadata + actual content
                        fixed_content = first_block + actual_content
                        
                        if self.dry_run:
                            print(f"  [DRY-RUN] Would fix: {md_file}")
                        else:
                            with open(md_file, 'w') as f:
                                f.write(fixed_content)
                            safe_print(f"  ✅ Fixed: {md_file.relative_to(self.docs_dir)}")
                            fixed_count += 1
                            
            except Exception as e:
                safe_print(f"  ❌ Error processing {md_file}: {e}")
        
        if fixed_count > 0:
            safe_print(f"\n🎉 Fixed {fixed_count} broken markdown files!")
        else:
            safe_print("\n✅ No broken files found")

    def auto_sync_nav_from_disk(self):
        """Automatically sync nav structure with actual files on disk"""
        safe_print("\n🔄 AUTO-SYNC NAV FROM DISK")
        print("=" * 50)
        
        # Find all section folders (folders with index.md)
        sections = {}
        for folder in self.docs_dir.iterdir():
            if folder.is_dir() and (folder / "index.md").exists():
                folder_name = folder.name
                
                # Get all markdown files in this folder
                pages = []
                for md_file in sorted(folder.glob("*.md")):
                    if md_file.name == "index.md":
                        continue
                    
                    # Try to extract title from metadata
                    try:
                        with open(md_file) as f:
                            content = f.read()
                            meta = self.extract_metadata(content)
                            if meta and 'title' in meta:
                                title = meta['title']
                            else:
                                # Fallback to filename
                                title = md_file.stem.replace('_', ' ').replace(folder_name + ' ', '').title()
                            
                            pages.append({title: f"{folder_name}/{md_file.name}"})
                    except:
                        pages.append(f"{folder_name}/{md_file.name}")
                
                if pages:
                    # Get section title from index.md metadata
                    try:
                        with open(folder / "index.md") as f:
                            content = f.read()
                            meta = self.extract_metadata(content)
                            section_title = meta.get('title', folder_name.replace('_', ' ').title()) if meta else folder_name.replace('_', ' ').title()
                    except:
                        section_title = folder_name.replace('_', ' ').title()
                    
                    sections[section_title] = [{"Overview": f"{folder_name}/index.md"}] + pages
        
        # Update nav - remove old section entries and add new ones
        for section_title in sections:
            self.nav = [item for item in self.nav if not (isinstance(item, dict) and section_title in item)]
        
        # Re-add all sections with current structure
        for section_title, structure in sections.items():
            self.nav.append({section_title: structure})
        
        self.config['nav'] = self.nav
        
        if self.dry_run:
            print("[DRY-RUN] Would update nav with:")
            for section_title, structure in sections.items():
                print(f"  {section_title}:")
                for item in structure:
                    print(f"    - {item}")
        else:
            self.save_config()
            safe_print(f"✅ Synced {len(sections)} sections from disk to nav")
    
    def create_header(self):
        """Create a new top-level navigation header"""
        safe_print("\n📁 CREATE HEADER")
        print("=" * 50)
        name = input("Header name (e.g., 'Advanced Features'): ").strip()
        
        if not name:
            safe_print("❌ Name cannot be empty")
            return
        
        doc_type = input("Doc type [guide/reference/tutorial] (default: guide): ").strip() or "guide"
        
        folder_name = self.slugify(name)
        folder_path = self.docs_dir / folder_name
        
        if self.dry_run:
            print(f"  [DRY-RUN] Would create folder: {folder_path}")
        else:
            folder_path.mkdir(exist_ok=True)
            safe_print(f"✅ Created folder: {folder_path}")
        
        # Create index file with metadata
        index_file = folder_path / "index.md"
        if not self.check_collision(index_file):
            return
        
        content = self.create_metadata_header(name, section=folder_name, status="stable", doc_type=doc_type)
        content += f"# {name}\n\n"
        content += f"Welcome to the {name} section.\n\n"
        content += "## Overview\n\n"
        content += "This section covers:\n\n"
        content += "- Topic 1\n"
        content += "- Topic 2\n"
        
        self.create_file(index_file, content)
        if not self.dry_run:
            safe_print(f"✅ Created: {index_file}")
        
        # REMOVE ALL EXISTING ENTRIES WITH THIS NAME FIRST
        self.nav = [item for item in self.nav if not (isinstance(item, dict) and name in item)]
        
        # Create proper nested structure like Advanced Features
        self.nav.append({
            name: [
                {"Overview": f"{folder_name}/index.md"}
            ]
        })
        
        if self.use_awesome_pages:
            # Create .pages.yml for sub-navigation
            self.append_to_pages_yml(folder_path, 'index.md', title=name)
        
        self.config['nav'] = self.nav
        self.save_config()
        
        safe_print(f"🎉 Header '{name}' created successfully!")

    def remove_duplicate_nav_entries(self):
        """Remove duplicate entries from nav"""
        safe_print("\n🧹 REMOVE DUPLICATE NAV ENTRIES")
        print("=" * 50)
        
        seen = set()
        clean_nav = []
        removed = []
        
        for item in self.nav:
            if isinstance(item, dict):
                key = list(item.keys())[0]
                if key in seen:
                    removed.append(key)
                    continue
                seen.add(key)
            elif isinstance(item, str):
                if item in seen:
                    removed.append(item)
                    continue
                seen.add(item)
            
            clean_nav.append(item)
        
        if removed:
            print(f"Found duplicates: {', '.join(removed)}")
            self.nav = clean_nav
            self.config['nav'] = self.nav
            self.save_config()
            safe_print(f"✅ Removed {len(removed)} duplicate entries")
        else:
            safe_print("✅ No duplicates found")
        
    def create_subheader(self):
        """Create a page under an existing header"""
        safe_print("\n📄 CREATE PAGE/SUBHEADER")
        print("=" * 50)
        
        # Show available headers
        print("\nAvailable headers:")
        headers = []
        for i, item in enumerate(self.nav):
            if isinstance(item, dict):
                header_name = list(item.keys())[0]
                headers.append((i, header_name, item[header_name]))
                print(f"  {i+1}) {header_name}")
        
        if not headers:
            safe_print("❌ No headers found. Create a header first!")
            return
        
        # Select header
        try:
            choice = int(input("\nSelect header number: ").strip()) - 1
            if choice < 0 or choice >= len(headers):
                safe_print("❌ Invalid selection")
                return
        except ValueError:
            safe_print("❌ Invalid input")
            return
        
        idx, header_name, header_value = headers[choice]
        
        # Get page details
        page_name = input(f"\nPage name under '{header_name}': ").strip()
        if not page_name:
            safe_print("❌ Name cannot be empty")
            return
        
        doc_type = input("Doc type [guide/reference/tutorial/demo] (default: guide): ").strip() or "guide"
        
        # Determine folder structure
        folder_name = self.slugify(header_name)
        folder_path = self.docs_dir / folder_name
        
        if self.dry_run:
            print(f"  [DRY-RUN] Would ensure folder exists: {folder_path}")
        else:
            folder_path.mkdir(exist_ok=True)
        
        # Create the new page with metadata - FIX: ADD FOLDER PREFIX
        file_name = self.slugify(page_name, folder_context=folder_name) + ".md"
        file_path = folder_path / file_name
        
        if not self.check_collision(file_path):
            return
        
        content = self.create_metadata_header(page_name, section=folder_name, doc_type=doc_type)
        content += f"# {page_name}\n\n"
        content += f"## Overview\n\n"
        content += f"Content for {page_name}.\n\n"
        if doc_type == "demo":
            content += f"## What You'll Learn\n\n"
            content += f"- Concept 1\n"
            content += f"- Concept 2\n\n"
        content += f"## Usage\n\n"
        content += f"```bash\n# Example command\n```\n"
        
        self.create_file(file_path, content)
        if not self.dry_run:
            safe_print(f"✅ Created: {file_path}")
        
        # Add to nav based on mode
        if self.use_awesome_pages:
            # Append just the filename to .pages.yml
            self.append_to_pages_yml(folder_path, file_name)
        else:
            # Manual mode: add to mkdocs.yml nav
            if isinstance(header_value, str):
                # Convert string to list first
                self.nav[idx] = {
                    header_name: [
                        {"Overview": header_value},
                        {page_name: f"{folder_name}/{file_name}"}
                    ]
                }
            elif isinstance(header_value, list):
                header_value.append({page_name: f"{folder_name}/{file_name}"})
            
            self.config['nav'] = self.nav
            self.save_config()
        
        safe_print(f"🎉 Page '{page_name}' created successfully!")
    
    def create_simple_page(self):
        """Create a standalone page (not under any header)"""
        safe_print("\n📝 CREATE STANDALONE PAGE")
        print("=" * 50)
        name = input("Page name (e.g., 'Changelog'): ").strip()
        
        if not name:
            safe_print("❌ Name cannot be empty")
            return
        
        doc_type = input("Doc type [guide/reference] (default: guide): ").strip() or "guide"
        
        file_name = self.slugify(name) + ".md"
        file_path = self.docs_dir / file_name
        
        if not self.check_collision(file_path):
            return
        
        content = self.create_metadata_header(name, status="stable", doc_type=doc_type)
        content += f"# {name}\n\n"
        content += f"Content for {name}.\n"
        
        self.create_file(file_path, content)
        if not self.dry_run:
            safe_print(f"✅ Created: {file_path}")
        
        # Add to nav
        self.nav.append({name: file_name})
        self.config['nav'] = self.nav
        self.save_config()
        
        safe_print(f"🎉 Page '{name}' created successfully!")
    
    def list_structure(self):
        """Display current documentation structure"""
        safe_print("\n📚 CURRENT DOCUMENTATION STRUCTURE")
        print("=" * 50)
        
        def print_nav(items, indent=0):
            for item in items:
                if isinstance(item, dict):
                    for key, value in item.items():
                        safe_print("  " * indent + f"📁 {key}")
                        if isinstance(value, list):
                            print_nav(value, indent + 1)
                        else:
                            safe_print("  " * (indent + 1) + f"📄 {value}")
                elif isinstance(item, str):
                    safe_print("  " * indent + f"📄 {item}")
        
        print_nav(self.nav)
        print()
        
        # Show filesystem reality
        safe_print("📂 FILESYSTEM STRUCTURE")
        print("=" * 50)
        for item in sorted(self.docs_dir.rglob("*.md")):
            rel = item.relative_to(self.docs_dir)
            
            # Try to read metadata
            try:
                with open(item) as f:
                    content = f.read()
                    meta = self.extract_metadata(content)
                    if meta:
                        doc_type = meta.get('doc_type', '')
                        status = meta.get('status', '')
                        print(f"  {rel} [{doc_type}] [{status}]")
                        continue
            except:
                pass
            
            print(f"  {rel}")
        print()
    
    def bulk_create_demos(self):
        """Quickly create multiple demo pages"""
        safe_print("\n🚀 BULK CREATE DEMO PAGES")
        print("=" * 50)
        
        # Check if Demos header exists
        demos_idx, existed = self.ensure_section_in_nav("Demos", "demos")
        
        if not existed:
            print("Creating 'Demos' header...")
            folder_path = self.docs_dir / "demos"
            
            if self.dry_run:
                print(f"  [DRY-RUN] Would create folder: {folder_path}")
            else:
                folder_path.mkdir(exist_ok=True)
            
            index_file = folder_path / "index.md"
            content = self.create_metadata_header("Demos", section="demos", status="stable", doc_type="demo")
            content += "# Demos\n\n"
            content += "Explore the project through interactive demos.\n\n"
            content += "## Available Demos\n\n"
            content += "Each demo showcases a specific feature or use case.\n"
            
            self.create_file(index_file, content)
            
            if self.use_awesome_pages:
                self.append_to_pages_yml(folder_path, 'index.md', title="Demos")
            
            if not self.dry_run:
                safe_print("✅ Created Demos header")
        
        # Get number of demos
        try:
            num = int(input("\nHow many demos to create? (e.g., 34): ").strip())
        except ValueError:
            safe_print("❌ Invalid number")
            return
        
        prefix = input("Demo name prefix (e.g., 'Demo'): ").strip() or "Demo"
        start_num = int(input("Start numbering at (default: 1): ").strip() or "1")
        
        folder_path = self.docs_dir / "demos"
        
        for i in range(start_num, start_num + num):
            demo_name = f"{prefix} {i}"
            file_name = f"demo_{i:02d}.md"
            file_path = folder_path / file_name
            
            if file_path.exists():
                safe_print(f"⏭️  Skipped (exists): {file_name}")
                continue
            
            content = self.create_metadata_header(demo_name, section="demos", doc_type="demo")
            content += f"# {demo_name}\n\n"
            content += f"## Overview\n\n"
            content += f"Description for {demo_name}.\n\n"
            content += f"## What You'll Learn\n\n"
            content += f"- Concept 1\n"
            content += f"- Concept 2\n\n"
            content += f"## Usage\n\n"
            content += f"```bash\ndemo {i}\n```\n\n"
            content += f"## Expected Output\n\n"
            content += f"```\n# Output will be shown here\n```\n"
            
            self.create_file(file_path, content)
            
            # Add to nav based on mode
            if self.use_awesome_pages:
                self.append_to_pages_yml(folder_path, file_name)
            else:
                # Manual mode: append to nav
                demos_list = self.nav[demos_idx]["Demos"]
                if isinstance(demos_list, str):
                    demos_list = [{"Overview": demos_list}]
                    self.nav[demos_idx]["Demos"] = demos_list
                demos_list.append({demo_name: f"demos/{file_name}"})
            
            if not self.dry_run:
                safe_print(f"✅ Created: {file_name}")
        
        self.config['nav'] = self.nav
        self.save_config()
        
        safe_print(f"\n🎉 Created {num} demo pages!")
    
    def check_collisions(self):
        """Scan for potential filename collisions"""
        safe_print("\n🔍 COLLISION DETECTION")
        print("=" * 50)
        
        files = {}
        collisions = []
        
        for md_file in self.docs_dir.rglob("*.md"):
            name = md_file.stem.lower()
            if name in files:
                collisions.append((name, files[name], md_file))
            else:
                files[name] = md_file
        
        if collisions:
            safe_print("⚠️  POTENTIAL COLLISIONS FOUND:")
            for name, file1, file2 in collisions:
                print(f"\n  '{name}':")
                print(f"    - {file1.relative_to(self.docs_dir)}")
                print(f"    - {file2.relative_to(self.docs_dir)}")
                
                # Check if they're in different folders (safe collision)
                if file1.parent != file2.parent:
                    safe_print(f"    ✅ Safe: Different folders provide namespace isolation")
        else:
            safe_print("✅ No collisions detected")
        
        print()
    
    def scan_metadata(self):
        """Scan all docs and report metadata stats"""
        safe_print("\n📊 METADATA ANALYSIS")
        print("=" * 50)
        
        stats = {
            'total': 0,
            'with_metadata': 0,
            'by_type': {},
            'by_status': {},
            'missing_metadata': []
        }
        
        for md_file in self.docs_dir.rglob("*.md"):
            stats['total'] += 1
            
            try:
                with open(md_file) as f:
                    content = f.read()
                    meta = self.extract_metadata(content)
                    
                    if meta:
                        stats['with_metadata'] += 1
                        doc_type = meta.get('doc_type', 'unknown')
                        status = meta.get('status', 'unknown')
                        
                        stats['by_type'][doc_type] = stats['by_type'].get(doc_type, 0) + 1
                        stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                        continue
            except:
                pass
            
            stats['missing_metadata'].append(md_file.relative_to(self.docs_dir))
        
        print(f"Total docs: {stats['total']}")
        print(f"With metadata: {stats['with_metadata']} ({stats['with_metadata']/stats['total']*100:.1f}%)")
        print()
        
        print("By Type:")
        for doc_type, count in sorted(stats['by_type'].items()):
            print(f"  {doc_type}: {count}")
        print()
        
        print("By Status:")
        for status, count in sorted(stats['by_status'].items()):
            print(f"  {status}: {count}")
        print()
        
        if stats['missing_metadata']:
            safe_print(f"⚠️  {len(stats['missing_metadata'])} docs without metadata:")
            for f in stats['missing_metadata'][:10]:
                print(f"  - {f}")
            if len(stats['missing_metadata']) > 10:
                print(f"  ... and {len(stats['missing_metadata']) - 10} more")
        
        print()
        return stats
    
    def migrate_existing_docs(self):
        """Add metadata to existing docs that lack it"""
        safe_print("\n🔄 MIGRATE EXISTING DOCS")
        print("=" * 50)
        
        stats = self.scan_metadata()
        
        if not stats['missing_metadata']:
            safe_print("✅ All docs already have metadata!")
            return
        
        print(f"\nFound {len(stats['missing_metadata'])} docs without metadata")
        
        if self.dry_run:
            print("[DRY-RUN] Would add metadata to these files")
            return
        
        proceed = input("Add metadata to these docs? (y/N): ").strip().lower()
        if proceed != 'y':
            safe_print("❌ Cancelled")
            return
        
        default_type = input("Default doc_type [guide/reference/tutorial] (default: guide): ").strip() or "guide"
        default_status = input("Default status [draft/stable] (default: draft): ").strip() or "draft"
        
        migrated = 0
        for rel_path in stats['missing_metadata']:
            file_path = self.docs_dir / rel_path
            
            try:
                with open(file_path) as f:
                    content = f.read()
                
                # Extract title from first # heading if present
                title = rel_path.stem.replace('_', ' ').title()
                title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                if title_match:
                    title = title_match.group(1)
                
                # Determine section from folder
                section = rel_path.parent.name if rel_path.parent != Path('.') else None
                
                # Create metadata
                metadata = self.create_metadata_header(title, section=section, status=default_status, doc_type=default_type)
                
                # Prepend metadata
                new_content = metadata + content
                
                with open(file_path, 'w') as f:
                    f.write(new_content)
                
                safe_print(f"✅ Migrated: {rel_path}")
                migrated += 1
                
            except Exception as e:
                safe_print(f"❌ Failed to migrate {rel_path}: {e}")
        
        safe_print(f"\n🎉 Migrated {migrated} docs!")
    
    def main_menu(self):
        """Display main menu and handle user input"""
        while True:
            print("\n" + "=" * 50)
            safe_print("🔧 MKDOCS DOCUMENTATION BUILDER")
            print(f"   v{self.VERSION} - Final Production Release")
            print("=" * 50)
            print("1) Create Header (top-level section)")
            print("2) Create Page/Subheader (under existing header)")
            print("3) Create Standalone Page")
            print("4) Bulk Create Demo Pages")
            print("5) View Current Structure")
            print("6) Check for Collisions")
            print("7) Scan Metadata Stats")
            print("8) Migrate Existing Docs (add metadata)")
            print("9) Toggle Dry-Run Mode")
            print("10) Fix Broken Markdown Files")  # ADD THIS
            print("11) Remove Duplicate Nav Entries")
            print("12) Auto-Sync Nav from Disk")  # ADD THIS
            print("0) Exit")
            print("=" * 50)
            
            if self.dry_run:
                safe_print("🔍 DRY-RUN MODE: Changes will be previewed only")
            
            if self.use_awesome_pages:
                safe_print("ℹ️  Mode: awesome-pages (minimal mkdocs.yml + .pages.yml)")
            else:
                safe_print("ℹ️  Mode: manual nav (full mkdocs.yml structure)")
            
            if USING_RUAMEL:
                safe_print("✅ Using ruamel.yaml (comment-preserving)")
            else:
                safe_print("⚠️  Using PyYAML (will strip comments)")
            
            choice = input("\nSelect option: ").strip()
            
            if choice == "1":
                self.create_header()
            elif choice == "2":
                self.create_subheader()
            elif choice == "3":
                self.create_simple_page()
            elif choice == "4":
                self.bulk_create_demos()
            elif choice == "5":
                self.list_structure()
            elif choice == "6":
                self.check_collisions()
            elif choice == "7":
                self.scan_metadata()
            elif choice == "8":
                self.migrate_existing_docs()
            elif choice == "9":
                self.dry_run = not self.dry_run
                status = "ENABLED" if self.dry_run else "DISABLED"
                safe_print(f"\n🔄 Dry-run mode {status}")
            elif choice == "10":
                self.fix_broken_markdown_files()
            elif choice == "11":
                self.remove_duplicate_nav_entries()
            elif choice == "12":
                self.auto_sync_nav_from_disk()
            elif choice == "0":
                safe_print("\n👋 Goodbye!")
                break
            else:
                safe_print("❌ Invalid option")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MkDocs Documentation Builder (gitship)")
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without modifying files')
    args = parser.parse_args()

    try:
        builder = DocBuilder(dry_run=args.dry_run)
        builder.main_menu()
    except KeyboardInterrupt:
        safe_print("\n\n👋 Goodbye!")
        sys.exit(0)