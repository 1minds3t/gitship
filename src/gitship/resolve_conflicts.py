#!/usr/bin/env python3
"""
Merge Conflict Resolver - Interactive conflict resolution helper
Shows conflicts, offers ours/theirs/manual choices
"""

import os
import sys
import subprocess
import re
from pathlib import Path
from typing import List, Tuple

def run_git(args, check=True):
    """Run git command and return stdout."""
    try:
        res = subprocess.run(
            ["git"] + args, capture_output=True, text=True, check=check, encoding='utf-8', errors='replace'
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        if not check: return ""
        print(f"Git error: {e.stderr}")
        return ""

def get_conflicted_files() -> List[str]:
    """Get list of files with merge conflicts."""
    output = run_git(["diff", "--name-only", "--diff-filter=U"])
    return [f for f in output.split('\n') if f]

def parse_conflict_blocks(content: str) -> List[dict]:
    """Parse conflict markers and extract blocks."""
    blocks = []
    lines = content.split('\n')
    i = 0
    
    while i < len(lines):
        if lines[i].startswith('<<<<<<<'):
            # Found conflict start
            conflict_start = i
            ours_label = lines[i].replace('<<<<<<<', '').strip()
            
            # Find middle marker
            middle = i + 1
            while middle < len(lines) and not lines[middle].startswith('======='):
                middle += 1
            
            # Find end marker
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
    print(block['ours'])
    
    print(f"\n🔴 THEIRS (REMOTE - {block['theirs_label']}):")
    print("─" * 80)
    print(block['theirs'])
    
    print("\n" + "=" * 80)

def resolve_conflict_interactive(filepath: str):
    """Interactively resolve conflicts in a file."""
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return False
    
    content = path.read_text()
    blocks = parse_conflict_blocks(content)
    
    if not blocks:
        print(f"No conflicts found in {filepath}")
        return False
    
    print(f"\n📝 Found {len(blocks)} conflict(s) in {filepath}")
    print("\nHow do you want to resolve this file?")
    print("  V - VIEW full diff first")
    print("  F - Save full diff to FILE")
    print("  O - Keep ALL blocks as OURS (local)")
    print("  T - Keep ALL blocks as THEIRS (remote/incoming)")
    print("  B - Resolve BLOCK-BY-BLOCK (choose per conflict)")
    print("  S - Skip this file")
    print("  Q - Quit resolver")
    
    choice = input("\nChoice (V/F/O/T/B/S/Q): ").strip().upper()
    
    if choice == 'V':
        # Show diff
        diff_output = run_git(["diff", filepath], check=False)
        lines = diff_output.split('\n')
        line_count = len(lines)
        
        print(f"\n📊 Diff has {line_count} lines")
        
        # Always show preview first (first 50 lines)
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
                # Use less
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                
                print(f"\n📖 Opening in less...")
                print("   💡 Controls: arrows/pgup/pgdn to scroll, / to search, q to quit")
                print("   ⏳ Starting in 3 seconds...")
                import time
                time.sleep(3)
                subprocess.call(['less', '-R', temp_path])
                os.unlink(temp_path)
            
            elif view_choice == '2':
                # Cat it all
                print("\n" + "─" * 80)
                print(diff_output)
                print("─" * 80)
            
            elif view_choice == '3':
                # Nano
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                
                print(f"\n📖 Opening in nano...")
                print("   💡 Controls: Ctrl+X to exit")
                print("   ⏳ Starting in 2 seconds...")
                import time
                time.sleep(2)
                subprocess.call(['nano', temp_path])
                os.unlink(temp_path)
            
            elif view_choice == '4':
                # Vim
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8', errors='replace') as tf:
                    tf.write(diff_output)
                    temp_path = tf.name
                
                print(f"\n📖 Opening in vim...")
                print("   💡 Controls: :q to quit (or :q! to force quit)")
                print("   ⏳ Starting in 3 seconds... (good luck!)")
                import time
                time.sleep(3)
                subprocess.call(['vim', temp_path])
                os.unlink(temp_path)
            
            elif view_choice == '5':
                # Save to file
                output_file = f"conflict_{Path(filepath).name}.diff"
                with open(output_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(diff_output)
                print(f"\n✓ Saved to: {output_file}")
            
            # choice 6 just continues with preview
        
        # Ask again after viewing
        print("\nNow choose resolution:")
        print("  O - Keep ALL as OURS (local)")
        print("  T - Keep ALL as THEIRS (remote/incoming)")
        print("  B - Resolve BLOCK-BY-BLOCK")
        print("  S - Skip this file")
        choice = input("\nChoice (O/T/B/S): ").strip().upper()
    
    elif choice == 'F':
        # Save diff to file
        diff_output = run_git(["diff", filepath], check=False)
        output_file = f"conflict_{Path(filepath).name}.diff"
        with open(output_file, 'w', encoding='utf-8', errors='replace') as f:
            f.write(diff_output)
        print(f"\n✓ Saved diff to: {output_file}")
        
        # Ask what to do
        print("\nNow choose resolution:")
        print("  O - Keep ALL as OURS (local)")
        print("  T - Keep ALL as THEIRS (remote/incoming)")
        print("  B - Resolve BLOCK-BY-BLOCK")
        print("  S - Skip this file")
        choice = input("\nChoice (O/T/B/S): ").strip().upper()
    
    if choice == 'O':
        run_git(["checkout", "--ours", filepath])
        run_git(["add", filepath])
        print(f"✓ Kept ALL as OURS (local) in {filepath}")
        return True
        
    elif choice == 'T':
        run_git(["checkout", "--theirs", filepath])
        run_git(["add", filepath])
        print(f"✓ Kept ALL as THEIRS (remote/incoming) in {filepath}")
        return True
        
    elif choice == 'S':
        print(f"⏭️  Skipping {filepath}")
        return False
        
    elif choice == 'Q':
        print("👋 Quitting resolver")
        sys.exit(0)
        
    elif choice == 'B':
        # Block-by-block resolution
        lines = content.split('\n')
        resolved_lines = []
        last_end = -1
        
        for i, block in enumerate(blocks, 1):
            # Add lines before this conflict
            if last_end == -1:
                resolved_lines.extend(lines[0:block['start']])
            else:
                resolved_lines.extend(lines[last_end + 1:block['start']])
            
            # Show and resolve this conflict
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
                    resolved_lines.append(block['ours'])
                    print("✓ Keeping OURS (local)")
                    break
                elif block_choice == 'T':
                    resolved_lines.append(block['theirs'])
                    print("✓ Keeping THEIRS (remote)")
                    break
                elif block_choice == 'B':
                    resolved_lines.append(block['ours'])
                    resolved_lines.append(block['theirs'])
                    print("✓ Keeping BOTH")
                    break
                elif block_choice == 'E':
                    # Write current state and open editor
                    temp_content = '\n'.join(resolved_lines + lines[block['start']:])
                    path.write_text(temp_content)
                    editor = os.environ.get('EDITOR', 'nano')
                    subprocess.call([editor, filepath])
                    # Re-read after edit
                    content = path.read_text()
                    blocks = parse_conflict_blocks(content)
                    if not blocks:
                        print("✓ All conflicts resolved via editor")
                        run_git(["add", filepath])
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
        
        # Write resolved file
        path.write_text('\n'.join(resolved_lines))
        print(f"\n✅ Resolved all conflicts in {filepath}")
        
        # Stage the file
        run_git(["add", filepath])
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
            run_git(["checkout", "--ours", filepath])
        else:  # theirs
            run_git(["checkout", "--theirs", filepath])
        run_git(["add", filepath])
        print(f"✓ {filepath}")

def main():
    # Check if in rebase/merge/cherry-pick
    git_dir = Path(".git")
    in_rebase = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
    in_merge = (git_dir / "MERGE_HEAD").exists()
    in_cherry_pick = (git_dir / "CHERRY_PICK_HEAD").exists()
    
    if not in_rebase and not in_merge and not in_cherry_pick:
        print("❌ Not in a rebase, merge, or cherry-pick state")
        sys.exit(1)
    
    files = get_conflicted_files()
    
    if not files:
        print("✅ No conflicted files found!")
        if in_rebase:
            print("\nRun: git rebase --continue")
        elif in_cherry_pick:
            print("\nRun: git cherry-pick --continue")
        else:
            print("\nRun: git commit")
        sys.exit(0)
    
    print(f"\n🔀 Found {len(files)} file(s) with conflicts:")
    for f in files:
        print(f"  - {f}")
    
    print("\n" + "=" * 80)
    print("CONFLICT RESOLUTION OPTIONS")
    print("=" * 80)
    print("\n1. INTERACTIVE - Resolve each conflict individually (recommended)")
    print("2. BULK OURS   - Keep ALL local changes (discard remote)")
    print("3. BULK THEIRS - Keep ALL remote changes (discard local)")
    print("4. ABORT       - Abort the operation")
    
    choice = input("\nChoice (1-4): ").strip()
    
    if choice == '1':
        # Interactive mode
        for filepath in files:
            if not resolve_conflict_interactive(filepath):
                continue
        
        remaining = get_conflicted_files()
        if remaining:
            print(f"\n⚠️  Still have {len(remaining)} unresolved file(s)")
            for f in remaining:
                print(f"  - {f}")
        else:
            print("\n🎉 All conflicts resolved!")
            if in_rebase:
                print("\nNext step: git rebase --continue")
            elif in_cherry_pick:
                print("\nNext step: git cherry-pick --continue")
            else:
                print("\nNext step: git commit")
    
    elif choice == '2':
        print("\n⚠️  Keeping ALL local changes (OURS)")
        bulk_resolve_all(files, 'ours')
        print("\n✅ All conflicts resolved with OURS")
        if in_rebase:
            print("Next step: git rebase --continue")
        elif in_cherry_pick:
            print("Next step: git cherry-pick --continue")
        else:
            print("Next step: git commit")
    
    elif choice == '3':
        print("\n⚠️  Keeping ALL remote changes (THEIRS)")
        bulk_resolve_all(files, 'theirs')
        print("\n✅ All conflicts resolved with THEIRS")
        if in_rebase:
            print("Next step: git rebase --continue")
        elif in_cherry_pick:
            print("Next step: git cherry-pick --continue")
        else:
            print("Next step: git commit")
    
    elif choice == '4':
        if in_rebase:
            run_git(["rebase", "--abort"], check=False)
            print("✓ Rebase aborted")
        elif in_cherry_pick:
            run_git(["cherry-pick", "--abort"], check=False)
            print("✓ Cherry-pick aborted")
        else:
            run_git(["merge", "--abort"], check=False)
            print("✓ Merge aborted")
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()