"""Persistent JSON run state: load, save, and human-readable report.

One state file records the most recent run. It lets ``--report`` describe
that run and lets ``--reuse`` find the temp directory a ``--keep-temp``
run left behind. The file lives in ``$XDG_CACHE_HOME/asciify`` (default
``~/.cache/asciify``).

State record fields:

* ``version`` -- schema version, currently 1
* ``updated`` -- ISO timestamp of the last save
* ``input``, ``output`` -- paths of the run
* ``temp_dir`` -- reusable temp directory, or ``None``
* ``keep_temp``, ``completed`` -- run flags
* ``stages_completed`` -- stage names finished so far
* ``probe`` -- ``width``, ``height``, ``fps``, ``has_audio``
* ``key`` -- ``mode``, ``color``, ``detected``, ``tolerance``, ``edge_blending``
* ``grid`` -- ``ascii_width``, ``rows``, ``stretch``, ``ramp``
* ``render`` -- target size, font, colour and background settings
* ``counts`` -- ``frames``, ``ascii_files``, ``rendered_images``
* ``duration_s`` -- wall-clock seconds of a completed run
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

from asciify_video.constants import DEFAULT

#: A run-state record: the decoded contents of the state file.
State = dict[str, Any]

#: Directory holding the state file.
STATE_DIR: Path = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'asciify'

#: The state file itself.
STATE_FILE: Path = STATE_DIR / 'state.json'


def _empty_state() -> State:
    """Build a fresh state record with every known field present."""
    return {
        'version': 1, 'updated': None, 'input': None, 'temp_dir': None,
        'keep_temp': False, 'completed': False, 'stages_completed': [],
        'probe': None, 'key': None, 'grid': None, 'render': None,
        'counts': {}, 'output': None, 'duration_s': None,
    }


def load_state() -> State:
    """Read the state file, filling in any fields it lacks.

    Unknown keys are preserved, so a file written by a newer version
    survives a round trip. A missing or unparseable file yields a fresh
    record (with a warning on stdout in the unparseable case).

    Returns:
        The state record, always containing every known field.
    """
    if STATE_FILE.exists():
        try:
            return {**_empty_state(), **json.loads(STATE_FILE.read_text())}
        except (json.JSONDecodeError, OSError):
            print(f"  Warning: could not parse {STATE_FILE}; starting fresh")
    return _empty_state()


def save_state(state: State) -> None:
    """Write the state record to the state file.

    Args:
        state: Record to persist. Its ``updated`` field is set to the
            current time as a side effect.

    Raises:
        OSError: If the state directory or file cannot be written.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state['updated'] = datetime.datetime.now().isoformat(timespec='seconds')
    STATE_FILE.write_text(json.dumps(state, indent=2))


def count_dir(d: Path | None) -> int:
    """Count the entries directly inside a directory.

    Args:
        d: Directory to inspect; may be ``None`` or nonexistent.

    Returns:
        Number of entries, or 0 if ``d`` is not a directory.
    """
    return len(list(d.glob('*'))) if d and d.is_dir() else 0


def _report_run(state: State) -> None:
    """Print when, on what, and how far the last run got."""
    print(f"Updated:    {state.get('updated', '?')}")
    if state.get('input'):
        print(f"Input:      {state['input']}")
    tmp = state.get('temp_dir')
    if tmp:
        print(f"Temp dir:   {tmp}  "
              f"({'present' if Path(tmp).is_dir() else 'MISSING'})")
    print(f"Completed:  {'yes' if state.get('completed') else 'NO'}")
    print(f"Stages:     "
          f"{', '.join(state.get('stages_completed') or []) or '(none)'}")


def _report_source_and_key(state: State) -> None:
    """Print the probed source properties and keying settings."""
    p = state.get('probe') or {}
    if p:
        print(f"Source:     {p.get('width')}x{p.get('height')} @ "
              f"{p.get('fps', 0):.2f} fps, "
              f"audio={'yes' if p.get('has_audio') else 'no'}")

    k = state.get('key') or {}
    if k.get('mode') == 'none':
        print("Keying:     skipped (--no-key)")
    elif k.get('color'):
        note = ' (auto-detected)' if k.get('detected') else ''
        print(f"Keying:     {k['color']}{note}, tol={k.get('tolerance')}, "
              f"blend={k.get('edge_blending')}")


def _report_grid(state: State) -> None:
    """Print the character grid dimensions and conversion settings."""
    g = state.get('grid') or {}
    if g.get('ascii_width'):
        s = g.get('stretch')
        stretch_s = f"{s[0]}-{s[1]}" if s else 'off'
        print(f"Grid:       {g['ascii_width']} cols x {g.get('rows')} rows, "
              f"stretch={stretch_s}, ramp='{g.get('ramp', 'default')}'")


def _report_render(state: State) -> None:
    """Print the output size, glyph colour and background settings."""
    r = state.get('render') or {}
    if not r.get('target_width'):
        return
    if r.get('colorized'):
        color_note = 'source'
    else:
        color_note = r.get('font_color', DEFAULT['FONT-COLOR'])
    print(f"Render:     {r['target_width']}x{r['target_height']}, "
          f"font={r.get('font_size')}, color={color_note}, "
          f"black-empty={'on' if r.get('leave_black_empty') else 'off'}"
          + (f" thr={r.get('black_threshold')}"
             if r.get('leave_black_empty') else ""))

    bg = r.get('bg_color')
    if bg:
        alpha = r.get('bg_alpha')
        print(f"Background: {bg}, alpha={alpha:.2f}"
              + ("  (transparent WebM)" if alpha is not None and alpha < 1.0
                 else ""))


def _report_artifacts(state: State) -> None:
    """Print artifact counts and the last output path."""
    c = state.get('counts') or {}
    if any(v is not None for v in c.values()):
        print(f"Artifacts:  frames={c.get('frames')}, "
              f"ascii={c.get('ascii_files')}, "
              f"rendered={c.get('rendered_images')}")

    if state.get('output'):
        print(f"Last output: {state['output']}")


def report_state(state: State) -> None:
    """Print a human-readable summary of the last run to stdout.

    Sections with no recorded data are omitted. Ends with a hint on
    whether the recorded temp directory can be used with ``--reuse``.

    Args:
        state: Record to describe, normally from :func:`load_state`.
    """
    print(f"State file: {STATE_FILE}")
    if not STATE_FILE.exists():
        print("  (no state recorded yet)")
        return
    _report_run(state)
    _report_source_and_key(state)
    _report_grid(state)
    _report_render(state)
    _report_artifacts(state)

    tmp = state.get('temp_dir')
    if tmp and Path(tmp).is_dir():
        print(f"\nReuse:      {sys.argv[0]} IN OUT --reuse "
              f"extracted|ascii|final")
    else:
        print("\nNo reusable temp dir — run with --keep-temp to enable "
              "--reuse")
