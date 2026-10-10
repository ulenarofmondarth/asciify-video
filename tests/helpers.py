"""Shared builders and CLI drivers for the asciify_video test suite."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from asciify_video import cli, constants, pipeline, render, state

RAMP = constants.DEFAULT['RAMP']


def gray_png(path, value, w=8, h=4):
    """Write a grayscale PNG that reads back as `value` luminance."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('L', (w, h), value).save(str(path))
    return path


def cell_png(path, values, cols=None, rows=None):
    """PNG whose pixel (x, y) has luminance values[y][x].

    Sized exactly cols x rows so _process_one_frame's BOX resize is an
    identity operation — per-character expectations stay exact.
    """
    rows = rows if rows is not None else len(values)
    cols = cols if cols is not None else len(values[0])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('L', (cols, rows))
    img.putdata([v for row in values for v in row])
    img.save(str(path))
    return path


def build_metrics(max_cols, max_rows, tw, th, font_size=32):
    """Grid geometry as run_conversion() derives it."""
    return render.GridMetrics.fit(max_cols, max_rows, tw, th, font_size)


def make_task(frame_path, out_path, ramp=RAMP, stretch=None, metrics=None,
              ascii_path=None, colorized=False,
              leave_black_empty=False, black_threshold=50,
              bg_color_rgb=(0, 0, 0), bg_alpha=1.0,
              font_color_rgb=(100, 255, 100)):
    return render.FrameTask(
        frame_path=str(frame_path), out_path=str(out_path),
        metrics=metrics or build_metrics(4, 2, 40, 20), ramp=ramp,
        stretch=stretch, ascii_path=ascii_path,
        leave_black_empty=leave_black_empty,
        black_threshold=black_threshold, colorized=colorized,
        bg_color_rgb=bg_color_rgb, bg_alpha=bg_alpha,
        font_color_rgb=font_color_rgb)


def expected_char(value, ramp=RAMP, stretch=None):
    """Mirror the luminance->ramp mapping in _process_one_frame."""
    if stretch is not None:
        lo, hi = stretch
        span = max(hi - lo, 1)
        value = min(255, max(0, (value - lo) * 255 // span))
    scale = len(ramp) / 256
    return ramp[min(len(ramp) - 1, int(value * scale))]


def make_artifacts(root, n_frames=3, with_txt=True, with_images=True,
                   cols=8, rows=4):
    """Build a directory tree shaped like a --keep-temp run."""
    root = Path(root)
    for i in range(1, n_frames + 1):
        gray_png(root / 'frames' / f'frame_{i:06d}.png', 128, cols, rows)
    if with_txt:
        line = expected_char(128) * cols
        (root / 'txt_frames').mkdir(parents=True, exist_ok=True)
        for i in range(n_frames):
            (root / 'txt_frames' / f'frame_{i:06d}.txt').write_text(
                '\n'.join([line] * rows))
    if with_images:
        (root / 'images').mkdir(parents=True, exist_ok=True)
        for i in range(n_frames):
            Image.new('RGB', (64, 32), 'black').save(
                str(root / 'images' / f'rendered_{i:06d}.png'))
    return root


def isolated_state(monkeypatch, tmp_path):
    """Keep tests away from the developer's real ~/.cache/asciify state."""
    state_file = tmp_path / 'state.json'
    monkeypatch.setattr(state, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(state, 'STATE_FILE', state_file)
    return state_file


def ffprobe_stdout(width=1920, height=1080, rate='30/1', audio=True):
    streams = [{'codec_type': 'video', 'width': width, 'height': height,
                'r_frame_rate': rate}]
    if audio:
        streams.append({'codec_type': 'audio'})
    return json.dumps({'streams': streams})


def invoke_main(argv):
    """Run the CLI main() and normalize exit semantics.

    Handles both return codes (pipeline errors surface as return 1)
    and argparse's SystemExit (bad usage / sys.exit in validators).
    """
    try:
        return cli.main(argv)
    except SystemExit as e:
        return e.code


def call_main(monkeypatch, tmp_path, argv, state_exists=False,
              state_body=None):
    """Invoke the CLI with a controllable argv, cwd and state file
    contained to tmp_path. probe_video is replaced with an exception so
    the run halts right after the pre-flight checks we want to observe.

    The input file named in argv is created as an empty stub so
    preflight existence checks pass regardless of its extension.
    Interception works because run_conversion() looks probe_video up
    in the pipeline module namespace.
    Returns the exit code (return value or SystemExit code).
    """
    parsed = cli.build_parser().parse_args(argv)
    if parsed.input:
        (tmp_path / parsed.input).write_bytes(b'')
    monkeypatch.chdir(tmp_path)
    state_file = isolated_state(monkeypatch, tmp_path)
    if state_exists:
        state_file.write_text(json.dumps(state_body or {}))
    monkeypatch.setattr(
        pipeline, 'probe_video',
        MagicMock(side_effect=RuntimeError('HALT_AFTER_PREFLIGHT')))
    return invoke_main(argv)
