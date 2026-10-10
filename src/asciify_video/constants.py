"""Default option values, character ramps, and stage/flag tables.

Everything here is plain data shared by the command-line front end and
the processing pipeline; nothing in this module has behaviour.
"""

from __future__ import annotations

from typing import Any

#: Default value for every tunable option, keyed by an upper-case option name.
#:
#: ``RAMP`` is ordered from darkest to brightest glyph. ``KEY-COLOR`` is a
#: display label only: the real default is ``None``, meaning "detect".
DEFAULT: dict[str, Any] = {
    'RAMP': " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    'FONT-COLOR': '#64FF64',
    'KEY-COLOR': 'auto-detect',
    'TOLERANCE': 0.25,
    'EDGE-BLEEDING': 0.05,
    'SCALE-DIVISOR': 8,
    'BLACK-THRESHOLD': 50,
    'SAMPLE-REGION': 'corners',
    'RASTERIZATION-QUALITY': 64,
    'BACKGROUND-COLOR': '#000000',
    'BACKGROUND-ALPHA': 1,
    'VPX-PRESET': 'ultrafast',
}

#: The shorter luminance ramp used by the ``jp2a`` tool, selected with ``--ramp jp2a``.
JP2A_RAMP: str = " .',;:clodxkO0KXNWM"

# ============================================================================
# Reuse Semantics Mapping
# Maps CLI options to their pipeline stage for reuse-aware messaging
# ============================================================================

#: Command-line flags grouped by the pipeline stage group that consumes them.
STAGE_FLAG_MAP: dict[str, list[str]] = {
    'keying': [
        '--key-color', '--detect-green', '--tolerance',
        '--edge-blending', '--sample-region', '--no-key'
    ],
    'conversion': [
        '--ramp', '--hist-stretch', '--ascii-width', '--scale-divisor'
    ],
    'rendering': [
        '--render-font-size', '--colorized', '--font-color',
        '--leave-black-empty', '--black-threshold',
        '--background-color', '--background-alpha', '--jobs'
    ],
}

#: Stage groups (keys of :data:`STAGE_FLAG_MAP`) that each ``--reuse`` level skips.
REUSE_SKIPPED_STAGES: dict[str, list[str]] = {
    'extracted': ['keying'],
    'ascii':     ['keying', 'conversion'],
    'final':     ['keying', 'conversion', 'rendering'],
}
