"""Convert green-screen video into ASCII-art video.

The names re-exported here are the supported programmatic entry points:
build a :class:`ConversionConfig` and pass it to :func:`run_conversion`.
The ``asciify_video`` command in :mod:`asciify_video.cli` is a thin wrapper
that does exactly that.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from asciify_video.config import ConversionConfig
from asciify_video.pipeline import run_conversion

try:
    __version__ = version("asciify-video")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = ["ConversionConfig", "run_conversion", "__version__"]
