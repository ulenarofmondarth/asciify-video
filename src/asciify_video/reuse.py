"""Locating and validating artifacts kept from a previous ``--keep-temp`` run.

A kept temp directory holds up to three artifact sets, each usable as a
starting point for a later run:

* ``frames/`` -- extracted (keyed, downscaled) PNG frames
* ``txt_frames/`` -- one ASCII text file per frame
* ``images/`` -- fully rendered PNG frames
"""

from __future__ import annotations

import os
from pathlib import Path

from asciify_video.state import State

#: Subdirectories that must exist and be non-empty for each reuse level.
REQUIRED_ARTIFACTS: dict[str, list[str]] = {
    'extracted': ['frames'],
    'ascii':     ['frames', 'txt_frames'],
    'final':     ['images'],
}


def resolve_reuse_dir(explicit_dir: str | os.PathLike[str] | None,
                      state: State) -> Path | None:
    """Pick the temp directory to reuse.

    Sources are consulted in priority order: the explicit directory
    (``--reuse-dir``), the ``ASCII_VIDEO_TEMP`` environment variable,
    then the directory recorded in the state file. Only the first
    source that is set is considered; there is no fall-through if it
    does not exist.

    Args:
        explicit_dir: Directory given by the user, or ``None``.
        state: Run state as returned by
            :func:`~asciify_video.state.load_state`.

    Returns:
        The chosen directory, or ``None`` if no source is set or the
        chosen path is not an existing directory.
    """
    if explicit_dir:
        d = Path(explicit_dir)
    elif os.environ.get('ASCII_VIDEO_TEMP'):
        d = Path(os.environ['ASCII_VIDEO_TEMP'])
    elif state.get('temp_dir'):
        d = Path(state['temp_dir'])
    else:
        return None
    return d if d.is_dir() else None


def validate_reuse(reuse_dir: Path, level: str) -> None:
    """Verify the artifacts needed at a reuse level exist.

    Args:
        reuse_dir: Temp directory from a ``--keep-temp`` run.
        level: ``'extracted'``, ``'ascii'`` or ``'final'``.

    Raises:
        RuntimeError: If a required subdirectory is missing or empty.
        KeyError: If ``level`` is not a known reuse level.
    """
    for sub in REQUIRED_ARTIFACTS[level]:
        d = reuse_dir / sub
        if not d.is_dir() or not list(d.glob('*')):
            raise RuntimeError(
                f"--reuse {level}: missing or empty '{d}'. "
                f"Re-run with --keep-temp first, or point --reuse-dir at "
                f"a complete temp directory.")


def validate_reuse_frame_sizes(reuse_dir: Path, state: State,
                               target_width: int, target_height: int) -> bool:
    """Compare a reused rendered frame against the requested output size.

    Purely advisory: mismatches are reported on stdout and then handled
    by scaling during assembly. One frame is sampled; all frames of a
    run share a size.

    Args:
        reuse_dir: Temp directory whose ``images/`` are being reused.
        state: Run state, consulted for the size the frames were
            recorded as rendered at.
        target_width: Requested output width in pixels.
        target_height: Requested output height in pixels.

    Returns:
        ``False`` if the sampled frame could not be read, otherwise
        ``True`` (including when there are no frames to check).
    """
    img_dir = reuse_dir / 'images'
    sample = next(img_dir.glob('rendered_*.png'), None)
    if not sample:
        return True   # nothing to validate

    try:
        from PIL import Image
        frame = Image.open(sample)
        fw, fh = frame.size
        frame.close()

        st_tw = state.get('render', {}).get('target_width')
        st_th = state.get('render', {}).get('target_height')

        # Check actual frame size against current target
        if fw != target_width or fh != target_height:
            print(f"  Warning: reused frames are {fw}x{fh}, "
                  f"target is {target_width}x{target_height}; "
                  f"will scale during assembly")

        # Also check against state-file claim (sanity check)
        if st_tw and st_th and (fw != st_tw or fh != st_th):
            print(f"  Note: state claims {st_tw}x{st_th}, "
                  f"frame is {fw}x{fh}; possible mismatch")
        return True
    except Exception as e:
        print(f"  Warning: Could not validate frame size: {e}")
        return False


def frame_num(p: str | os.PathLike[str]) -> int:
    """Extract the frame number from an artifact filename.

    Used as a sort key so frames order numerically regardless of
    zero-padding.

    Args:
        p: Path such as ``frame_000042.png`` or ``rendered_000042.png``.

    Returns:
        All digits in the file stem read as one integer, or 0 if the
        stem contains none.

    Example:
        >>> frame_num('frames/frame_000042.png')
        42
    """
    stem = Path(p).stem
    digits = ''.join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0
