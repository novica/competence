"""Create a database from a source checkout: `uv run seed.py [--demo] [--catalogue NAME|FILE] [--db FILE]`.

Same as `competence init`; databases go to this checkout's data/ folder.
"""

import sys

from competence.cli import main

if __name__ == "__main__":
    sys.exit(main(["init", *sys.argv[1:]]))
