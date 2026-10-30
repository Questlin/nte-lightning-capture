"""Entry point for running from source and for the PyInstaller build."""

import sys

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        from nte_lightning.selftest import run

        i = sys.argv.index("--self-test")
        sys.exit(run(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))

    from nte_lightning.app import main

    main()
