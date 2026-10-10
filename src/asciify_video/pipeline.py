"""Green-screen to ASCII video conversion pipeline.

:func:`run_conversion` drives seven numbered stages::

    1 probe -> 2 key colour -> 3 chromakey -> 4 extract
            -> 5+6 convert + render (fused, parallel) -> 7 assemble

Progress is recorded in the JSON state file after every stage, so an
interrupted run can still be inspected with ``--report``. With
``keep_temp`` the intermediate artifacts survive and a later run can
skip the stages that produced them (``reuse_level``):

* ``extracted`` -- start from the kept frames (skips stages 2-4)
* ``ascii`` -- also render from the kept ASCII text
* ``final`` -- only re-assemble the kept rendered frames
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from tqdm import tqdm

from asciify_video import state as state_store
from asciify_video.config import ConversionConfig
from asciify_video.constants import DEFAULT, JP2A_RAMP
from asciify_video.ffmpeg import (
    StrPath,
    assemble_video,
    chromakey_to_black,
    extract_frames,
    probe_video,
)
from asciify_video.keying import sample_green_color
from asciify_video.render import (
    GridMetrics,
    Stretch,
    compute_global_stretch,
    frames_to_rendered_images,
)
from asciify_video.reuse import frame_num, validate_reuse_frame_sizes
from asciify_video.state import State, count_dir, load_state, save_state

#: An output ``(width, height)`` in pixels.
Size = tuple[int, int]

_PROBE_FIELDS = ('width', 'height', 'fps', 'has_audio')


@dataclass(frozen=True)
class _Source:
    """Properties of the input video that drive the rest of the run.

    ``fps`` is the effective output rate: the configured override if
    there is one, otherwise the source rate.
    """

    width: int
    height: int
    fps: float
    has_audio: bool


def cleanup_temp(temp_dir: StrPath) -> None:
    """Delete a run's temp directory, ignoring anything that cannot be removed.

    Args:
        temp_dir: Directory to delete; need not exist.
    """
    shutil.rmtree(temp_dir, ignore_errors=True)


def _reuse_dir(cfg: ConversionConfig) -> Path:
    """Return the directory being reused; only valid on a reuse run."""
    if cfg.reuse_dir is None:
        raise ValueError(f"reuse_level={cfg.reuse_level!r} requires reuse_dir")
    return cfg.reuse_dir


def _resolve_ascii_width(cfg: ConversionConfig, target_width: int) -> int:
    """Character columns: explicit, else output width / scale divisor.

    Deriving from the output width (the source width unless a target
    resolution is set) keeps the grid identical across inputs rendered
    at the same target resolution, and makes each cell scale_divisor
    pixels wide.
    """
    if cfg.ascii_width is not None:
        return cfg.ascii_width
    return max(1, target_width // cfg.scale_divisor)


def _resolve_ramp(cfg: ConversionConfig) -> str:
    """Return the glyph ramp to use: named, custom, or the default."""
    if cfg.ramp == 'jp2a':
        return JP2A_RAMP
    return cfg.ramp or DEFAULT['RAMP']


# ------------------------------ Stage 1: probe ------------------------------

def _restorable_probe(cfg: ConversionConfig, state: State) -> State | None:
    """Probe results recorded by the run that produced the reuse dir, if any.

    Only trusted when the state file describes that same directory. The
    recorded fps is the rate the kept frames were extracted at, which is
    what reuse must assemble at (the source rate may differ if that run
    used an fps override).
    """
    if not cfg.reuse_dir or state.get('temp_dir') != str(cfg.reuse_dir):
        return None
    recorded = state.get('probe') or {}
    if any(recorded.get(field) is None for field in _PROBE_FIELDS):
        return None
    return recorded


def _probe(cfg: ConversionConfig, state: State,
           restored: State | None) -> _Source:
    """Stage 1: read source properties (restored from state when reusing)."""
    print("[1/7] Probing input video...")
    info = restored
    if info:
        print(f"  Restored from state file: "
              f"{info['width']}x{info['height']} @ {info['fps']:.2f} fps, "
              f"audio={'yes' if info['has_audio'] else 'no'}")
    else:
        info = probe_video(cfg.input)
        print(f"  Resolution: {info['width']}x{info['height']}")
        print(f"  Frame rate: {info['fps']:.2f} fps")
        print(f"  Audio: {'Yes' if info['has_audio'] else 'No'}")

    source = _Source(width=info['width'], height=info['height'],
                     fps=cfg.fps_override if cfg.fps_override else info['fps'],
                     has_audio=info['has_audio'])
    state['probe'] = asdict(source)
    state['stages_completed'] += ['probe']
    save_state(state)
    print()
    return source


# ---------------------------- Stage 2-3: keying -----------------------------

def _select_key_color(cfg: ConversionConfig, source: _Source,
                      state: State) -> str:
    """Stage 2: choose the chromakey color (detected, manual, or none)."""
    print("[2/7] Green color selection...")
    was_detected = False
    if cfg.reuse_level:
        print(f"  Skipped (reuse: {cfg.reuse_level})")
        key_color = 'N/A (reused)'
    elif cfg.no_key:
        print("  Skipping key color detection (--no-key)")
        key_color = 'N/A'
    elif cfg.detect_green or cfg.key_color is None:
        key_color, was_detected = sample_green_color(
            cfg.input, region=cfg.sample_region,
            src_width=source.width, src_height=source.height,
            show_progress=not cfg.silent_progress)
    else:
        key_color = cfg.key_color

    if cfg.reuse_level or cfg.no_key:
        key_mode = 'none'
    elif was_detected:
        key_mode = 'auto'
    else:
        key_mode = 'manual'
    state['key'] = {'mode': key_mode,
                    'color': None if key_color.startswith('N/A') else key_color,
                    'detected': was_detected,
                    'tolerance': cfg.tolerance,
                    'edge_blending': cfg.edge_blending}
    state['stages_completed'] += ['key']
    save_state(state)
    if not cfg.reuse_level:
        print(f"  Using chromakey color: {key_color}"
              + (" (auto-detected)" if was_detected else ""))
    print()
    return key_color


def _chromakey(cfg: ConversionConfig, source: _Source, key_color: str,
               temp_dir: Path) -> Path | None:
    """Stage 3: key the input over black.

    Returns:
        The video to extract frames from: the keyed copy, the input
        itself with ``no_key``, or ``None`` when reusing.
    """
    keyed: Path | None = None
    if cfg.reuse_level:
        print(f"[3/7] Skipping chromakey/extraction (reuse: {cfg.reuse_level})")
    elif cfg.no_key:
        print("[3/7] Skipping chromakey (--no-key)")
        keyed = cfg.input
    else:
        print("[3/7] Applying chromakey and compositing over black...")
        keyed = temp_dir / 'chroma_keyed.mp4'
        with tqdm(total=1, desc="  Processing video",
                  disable=cfg.silent_progress) as pbar:
            chromakey_to_black(cfg.input, keyed, key_color,
                               cfg.tolerance, cfg.edge_blending,
                               source.width, source.height, source.fps)
            pbar.update(1)
        print(f"  Output: {keyed}")
    print()
    return keyed


# ---------------------------- Stage 4: extraction ---------------------------

def _obtain_frames(cfg: ConversionConfig, keyed: Path | None, fps: float,
                   ascii_width: int, temp_dir: Path, state: State) -> list[Path]:
    """Stage 4: extract frames, or pick up the ones in the reuse dir.

    Raises:
        RuntimeError: If there are no frames to work on.
    """
    if cfg.reuse_level:
        frames_dir = _reuse_dir(cfg) / 'frames'
        print(f"[4/7] Using existing frames: {frames_dir}")
        frames = sorted(frames_dir.glob('frame_*.png'), key=frame_num)
        print(f"  {len(frames)} frames")
    else:
        assert keyed is not None      # stage 3 always keys or passes the input through
        print("[4/7] Extracting frames...")
        print(f"  Extracting at {ascii_width}px wide "
              f"for 1:1 character mapping"
              + ("" if cfg.ascii_width is not None
                 else f" (output width / --scale-divisor {cfg.scale_divisor})"))
        frames = extract_frames(keyed, temp_dir / 'frames', fps, ascii_width)
    state['counts']['frames'] = len(frames)
    state['stages_completed'] += ['extract']
    save_state(state)
    print()

    if not frames:
        raise RuntimeError("No frames extracted/found — cannot continue")
    return frames


# ----------------------- Stage 5+6: convert and render ----------------------

def _global_stretch(cfg: ConversionConfig,
                    frames: Sequence[Path]) -> Stretch | None:
    """Histogram bounds for --hist-stretch; None when off or not applicable."""
    if not cfg.hist_stretch:
        return None
    if cfg.reuse_level == 'ascii':
        print("  Note: --hist-stretch affects conversion; "
              "ignored when reusing ascii text")
        return None
    print("  Computing global histogram bounds...")
    stretch = compute_global_stretch(frames)
    print(f"  Stretch range: {stretch[0]}-{stretch[1]} "
          f"(gain x{255 / max(stretch[1] - stretch[0], 1):.2f})")
    return stretch


def _reused_ascii_files(cfg: ConversionConfig,
                        frames: Sequence[Path]) -> tuple[list[Path], int, int]:
    """Locate reused ASCII text and read its grid size.

    Returns:
        ``(files aligned with frames, columns, rows)``.

    Raises:
        RuntimeError: If the number of text files and frames differ.
    """
    ascii_files = sorted((_reuse_dir(cfg) / 'txt_frames').glob('*.txt'),
                         key=frame_num)
    if len(ascii_files) != len(frames):
        raise RuntimeError(
            f"ascii/frame count mismatch: "
            f"{len(ascii_files)} txt vs {len(frames)} frames")
    first = ascii_files[0].read_text().rstrip('\n').split('\n')
    cols, rows = max(len(line) for line in first), len(first)
    if cfg.ascii_width is not None and cfg.ascii_width != cols:
        print(f"  Note: reused ASCII grid is {cols} cols; "
              f"--ascii-width={cfg.ascii_width} ignored")
    print(f"  Using {len(ascii_files)} reused ASCII files "
          f"(grid {cols}x{rows})")
    return ascii_files, cols, rows


def _convert_and_render(cfg: ConversionConfig, frames: Sequence[Path],
                        ramp: str, ascii_width: int, target: Size,
                        temp_dir: Path) -> tuple[list[str], GridMetrics, Stretch | None]:
    """Run the fused convert+render over all frames.

    Returns:
        ``(rendered image paths, grid metrics, stretch actually applied)``.
    """
    print("[5/7][6/7] Converting and rendering "
          "(fused, parallel)...")
    stretch = _global_stretch(cfg, frames)

    ascii_files: list[Path] | None = None
    txt_dir: Path | None = None
    if cfg.reuse_level == 'ascii':
        ascii_files, cols, rows = _reused_ascii_files(cfg, frames)
    else:
        # Fresh (or reuse 'extracted'): grid is analytic
        from PIL import Image
        _, frame_height = Image.open(frames[0]).size
        cols, rows = ascii_width, max(1, frame_height // 2)
        if cfg.keep_temp:
            txt_dir = temp_dir / 'txt_frames'
    metrics = GridMetrics.fit(cols, rows, *target, cfg.render_font_size)

    rendered_images = frames_to_rendered_images(
        frames, temp_dir / 'images', metrics, ramp, stretch,
        leave_black_empty=cfg.leave_black_empty,
        black_threshold=cfg.black_threshold,
        jobs=cfg.jobs, ascii_files=ascii_files, txt_dir=txt_dir,
        colorized=cfg.colorized,
        bg_color_rgb=cfg.background_rgb,
        bg_alpha=cfg.background_alpha,
        font_color_rgb=cfg.font_color_rgb,
        show_progress=not cfg.silent_progress)
    return rendered_images, metrics, stretch


def _reuse_rendered(cfg: ConversionConfig) -> list[Path]:
    """Collect the rendered frames kept by an earlier run (``--reuse final``)."""
    print("[5/7][6/7] Skipping conversion+rendering "
          "(reuse: final)")
    rendered_images = sorted((_reuse_dir(cfg) / 'images')
                             .glob('rendered_*.png'), key=frame_num)
    print(f"  {len(rendered_images)} rendered frames")
    return rendered_images


def _record_render(cfg: ConversionConfig, state: State, ramp: str,
                   metrics: GridMetrics | None, stretch: Stretch | None,
                   target: Size, rendered_count: int) -> None:
    """Write the grid/render/count sections of the run state."""
    state['grid'] = {
        'ascii_width': metrics.cols if metrics else None,
        'rows': metrics.rows if metrics else None,
        'stretch': list(stretch) if stretch else None,
        'ramp': ('jp2a' if ramp == JP2A_RAMP
                 else ('default' if ramp == DEFAULT['RAMP'] else ramp)),
    }
    state['render'] = {
        'target_width': target[0], 'target_height': target[1],
        'font_size': cfg.render_font_size,
        'font_color': cfg.font_color,
        'colorized': cfg.colorized,
        'leave_black_empty': cfg.leave_black_empty,
        'black_threshold': cfg.black_threshold,
        'bg_color': cfg.background_color,
        'bg_alpha': cfg.background_alpha,
    }
    state['counts']['ascii_files'] = (
        count_dir(_reuse_dir(cfg) / 'txt_frames')
        if cfg.reuse_level == 'ascii' else state['counts']['frames'])
    state['counts']['rendered_images'] = rendered_count
    state['stages_completed'] += ['ascii', 'render']
    save_state(state)


def _render_stage(cfg: ConversionConfig, frames: Sequence[Path], ramp: str,
                  ascii_width: int, target: Size, temp_dir: Path,
                  state: State) -> Sequence[StrPath]:
    """Stages 5+6: produce (or reuse) rendered frames and record the result."""
    rendered_images: Sequence[StrPath]
    metrics: GridMetrics | None = None
    stretch: Stretch | None = None
    if cfg.reuse_level == 'final':
        rendered_images = _reuse_rendered(cfg)
    else:
        rendered_images, metrics, stretch = _convert_and_render(
            cfg, frames, ramp, ascii_width, target, temp_dir)
    _record_render(cfg, state, ramp, metrics, stretch, target,
                   len(rendered_images))
    print(f"  Rendered {len(rendered_images)} images")
    print()
    return rendered_images


# ----------------------------- Stage 7: assemble ----------------------------

def _assemble(cfg: ConversionConfig, rendered_images: Sequence[StrPath], source: _Source,
              target: Size, state: State) -> None:
    """Stage 7: encode the rendered frames (plus source audio) to the output."""
    print("[7/7] Assembling final video...")
    reusing_final = cfg.reuse_level == 'final'
    if reusing_final:
        # Resolution may differ from the run that created these frames
        validate_reuse_frame_sizes(_reuse_dir(cfg), state, *target)

    assemble_video(rendered_images, cfg.output, source.fps, cfg.input,
                   source.has_audio, *target,
                   check_frames=reusing_final,
                   bg_alpha=cfg.background_alpha,
                   vpx_preset=cfg.vpx_preset)
    print(f"  Final output: {cfg.output}")
    print()


def _run_stages(cfg: ConversionConfig, state: State, temp_dir: Path,
                restored_probe: State | None) -> None:
    """Run stages 1-7 in order, threading each stage's result to the next."""
    source = _probe(cfg, state, restored_probe)
    ramp = _resolve_ramp(cfg)
    key_color = _select_key_color(cfg, source, state)
    keyed = _chromakey(cfg, source, key_color, temp_dir)
    target = cfg.target_resolution or (source.width, source.height)
    ascii_width = _resolve_ascii_width(cfg, target[0])
    frames = _obtain_frames(cfg, keyed, source.fps, ascii_width, temp_dir, state)
    rendered_images = _render_stage(cfg, frames, ramp, ascii_width, target,
                                    temp_dir, state)
    _assemble(cfg, rendered_images, source, target, state)


def _reusable_dir(cfg: ConversionConfig) -> str | None:
    """The kept directory this run read from; it outlives the run."""
    return str(cfg.reuse_dir) if cfg.reuse_dir else None


def _record_completion(cfg: ConversionConfig, state: State, temp_dir: Path,
                       run_start: float) -> None:
    """Mark the run complete in the state file and print the closing banner."""
    print("="*60)
    print("CONVERSION COMPLETE!")
    print("="*60)
    state['output'] = str(cfg.output)
    state['completed'] = True
    state['duration_s'] = round(time.time() - run_start, 1)
    state['stages_completed'] += ['assemble']
    if cfg.keep_temp:
        state['temp_dir'] = str(temp_dir)
        print(f"\n  Temp directory (kept): {temp_dir}")
        print(f"  State file: {state_store.STATE_FILE}")
    else:
        state['temp_dir'] = _reusable_dir(cfg)
    save_state(state)
    print()


def run_conversion(cfg: ConversionConfig) -> Path:
    """Execute the full conversion pipeline.

    This is the programmatic entry point and the pure-execution
    counterpart to the CLI: it parses no arguments and validates no
    options, so ``cfg`` must already hold normalized values.

    Progress is printed to stdout and recorded in the state file as each
    stage completes. The run works in a fresh temp directory which is
    removed afterwards, on success or failure, unless ``cfg.keep_temp``
    is set. A directory being reused is never removed.

    Args:
        cfg: The run configuration.

    Returns:
        ``cfg.output``, the path of the video that was written.

    Raises:
        RuntimeError: If ffmpeg fails, no frames are produced, or reused
            artifacts are inconsistent.
        ValueError: If the input has no video stream, or
            ``cfg.reuse_level`` is set without ``cfg.reuse_dir``.
        OSError: If ffmpeg/ffprobe cannot be started or files cannot be
            read or written.
        KeyboardInterrupt: Re-raised after the state file is saved.

    Example:
        >>> from pathlib import Path
        >>> from asciify_video import ConversionConfig, run_conversion
        >>> cfg = ConversionConfig(input=Path('in.mp4'), output=Path('out.mp4'),
        ...                        colorized=True)
        >>> run_conversion(cfg)  # doctest: +SKIP
        PosixPath('out.mp4')
    """
    state = load_state()
    # Must be read before the reset below clears the recorded temp_dir
    restored_probe = _restorable_probe(cfg, state)
    run_start = time.time()
    temp_dir = Path(tempfile.mkdtemp(prefix='asciify_'))

    state.update({
        'input': str(cfg.input), 'keep_temp': cfg.keep_temp,
        'completed': False, 'stages_completed': [], 'counts': {},
        # A reused dir stays recorded so the next --reuse can still find it
        'temp_dir': _reusable_dir(cfg), 'output': None, 'duration_s': None,
    })
    save_state(state)

    try:
        print("\n" + "="*60)
        print("ASCIIFY")
        print("="*60 + "\n")
        _run_stages(cfg, state, temp_dir, restored_probe)
        _record_completion(cfg, state, temp_dir, run_start)
        return cfg.output

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        save_state(state)
        raise
    except Exception:
        if not cfg.keep_temp:
            print("Cleaning up temporary files...")
        save_state(state)
        raise
    finally:
        # Never clean a reused directory; only this run's own temp dir
        if not cfg.keep_temp:
            cleanup_temp(temp_dir)
