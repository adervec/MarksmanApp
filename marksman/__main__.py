"""Enable ``python -m marksman`` (same entry point as the ``marksman`` script)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
