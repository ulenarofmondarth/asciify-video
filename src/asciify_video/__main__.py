"""Allow ``python -m asciify_video`` as an alias for the ``asciify_video`` command."""

from __future__ import annotations

import sys

from asciify_video.cli import main

if __name__ == "__main__":
    sys.exit(main())
