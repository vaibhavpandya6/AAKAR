"""Interactive BRD-to-WBS test - prompts for BRD file path.

Just run: python test_interactive_brd.py
"""

import asyncio
from pathlib import Path

# Import the custom BRD test
from test_custom_brd import test_custom_brd


def main():
    """Interactive BRD file prompt."""

    print("=" * 70)
    print("INTERACTIVE BRD-TO-WBS TESTER")
    print("=" * 70)
    print()
    print("This will convert your BRD into a Work Breakdown Structure (WBS)")
    print("with tasks, test cases, and complete project documentation.")
    print()

    # Prompt for file
    while True:
        brd_path = input("Enter path to your BRD file (.md, .txt, .pdf, .docx): ").strip()

        # Remove quotes if user copy-pasted
        brd_path = brd_path.strip('"').strip("'")

        if not brd_path:
            print("   ⚠ Please enter a file path")
            continue

        path = Path(brd_path)

        if not path.exists():
            print(f"   ✗ File not found: {brd_path}")
            retry = input("   Try again? (y/n): ").strip().lower()
            if retry != 'y':
                return
            continue

        if path.suffix.lower() not in ['.md', '.txt', '.pdf', '.docx']:
            print(f"   ⚠ Warning: Unsupported file type {path.suffix}")
            print("   Supported: .md, .txt, .pdf, .docx")
            proceed = input("   Try anyway? (y/n): ").strip().lower()
            if proceed != 'y':
                continue

        break

    print()
    print(f"✓ File found: {path.name}")
    print()

    # Confirm
    print("This will:")
    print("  - Extract requirements, scope, architecture")
    print("  - Generate WBS tasks per module")
    print("  - Create test cases per agent")
    print("  - Take ~10-15 minutes")
    print()

    proceed = input("Proceed? (y/n): ").strip().lower()
    if proceed != 'y':
        print("Cancelled.")
        return

    print()

    # Run test
    try:
        asyncio.run(test_custom_brd(str(path)))
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted")
    except Exception as e:
        print(f"\n\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
