"""Run Competence from a source checkout: `uv run app.py [--demo] [--port N] [--reload]`.

Same as the installed `competence` command; databases stay in this checkout's data/ folder.
"""

import sys

from competence.cli import main

if __name__ == "__main__":
    sys.exit(main())
