"""Thin wrappers around the ``ffmpeg`` and ``ffprobe`` command-line tools.

Both binaries must be on ``PATH``. Every function here builds an
argument list and runs it through :func:`run_command`; none of them
parse video data in Python.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from asciify_video.constants import DEFAULT

#: A filesystem path given as a string or a path object.
StrPath = str | os.PathLike[str]

#: libvpx ``-cpu-used`` value for each named ``--vpx-preset`` (higher is faster).
VPX_PRESET_SPEEDS: dict[str, int] = {
    'ultrafast': 8,
    'veryfast': 6,
    'faster': 4,
    'fast': 2,
    'medium': 1,
    'slow': 0,
}

_DEFAULT_VPX_SPEED = 4


def run_command(cmd: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an external command, capturing its output as text.

    Args:
        cmd: Program and arguments.
        check: Raise if the command exits non-zero.

    Returns:
        The completed process, with ``stdout`` and ``stderr`` as strings.

    Raises:
        RuntimeError: If ``check`` is set and the command exits non-zero.
            The command's stderr is printed to stdout first.
        OSError: If the program cannot be started (for example, it is
            not installed).
    """
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"\nSTDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def probe_video(filepath: StrPath) -> dict[str, Any]:
    """Read the basic properties of a video file with ``ffprobe``.

    Args:
        filepath: Video file to inspect.

    Returns:
        A dict with ``width`` and ``height`` (int, pixels), ``fps``
        (float) and ``has_audio`` (bool). Missing stream fields fall
        back to 1280x720 at 30 fps.

    Raises:
        ValueError: If the file has no video stream.
        RuntimeError: If ``ffprobe`` fails, for example on an unreadable
            file.
    """
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', str(filepath)]
    info = json.loads(run_command(cmd).stdout)

    video_stream = next((s for s in info['streams']
                         if s['codec_type'] == 'video'), None)
    audio_stream = next((s for s in info['streams']
                         if s['codec_type'] == 'audio'), None)
    if not video_stream:
        raise ValueError("No video stream found in input file")

    width = int(video_stream.get('width', 1280))
    height = int(video_stream.get('height', 720))
    num, denom = video_stream.get('r_frame_rate', '30/1').split('/')
    fps = float(num) / float(denom)
    return {'width': width, 'height': height, 'fps': fps,
            'has_audio': audio_stream is not None}


def chromakey_to_black(input_path: StrPath, output_path: StrPath, key_color: str,
                       tolerance: float, edge_blending: float,
                       width: int, height: int, fps: float) -> StrPath:
    """Key out a colour and composite the result over black.

    Writes an H.264 video the same size as the input, with the audio
    stream (if any) copied unchanged.

    Args:
        input_path: Source video.
        output_path: File to write.
        key_color: Colour to remove, as an ffmpeg colour such as
            ``'0x00FF00'``.
        tolerance: Chromakey similarity threshold, 0-1.
        edge_blending: Chromakey blend amount, 0-1.
        width: Source width in pixels, for the black backdrop.
        height: Source height in pixels.
        fps: Frame rate of the black backdrop.

    Returns:
        ``output_path``.

    Raises:
        RuntimeError: If ``ffmpeg`` fails.
    """
    cmd = ['ffmpeg', '-stats', '-y', '-i', str(input_path),
           '-filter_complex',
           f'[0:v]chromakey={key_color}:{tolerance}:{edge_blending}[fg];'
           f'color=c=black:s={width}x{height}:r={fps}[bg];'
           '[bg][fg]overlay=shortest=1,format=yuv420p[v]',
           '-map', '[v]', '-map', '0:a?',
           '-c:a', 'copy', '-c:v', 'libx264', '-crf', '18',
           '-pix_fmt', 'yuv420p', str(output_path)]
    run_command(cmd, check=True)
    return output_path


def extract_frames(video_path: StrPath, frames_dir: Path,
                   target_fps: float | None = None,
                   ascii_width: int = 240) -> list[Path]:
    """Extract every frame as a PNG, downscaled to the ASCII grid width.

    Scaling to exactly ``ascii_width`` pixels gives a 1:1 mapping from
    pixel columns to character columns, so no further horizontal
    resampling is needed. Height keeps the aspect ratio.

    Args:
        video_path: Video to read.
        frames_dir: Directory to write ``frame_NNNNNN.png`` files into;
            created if missing.
        target_fps: Frame rate to resample to. ``None`` keeps every
            source frame.
        ascii_width: Width in pixels of the extracted frames.

    Returns:
        The extracted frame paths in frame order.

    Raises:
        RuntimeError: If ``ffmpeg`` fails.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    scale_filter = f'scale={ascii_width}:-2:flags=lanczos'
    if target_fps is not None:
        scale_filter += f',fps={target_fps}'
    cmd = ['ffmpeg', '-y', '-i', str(video_path), '-vf', scale_filter,
           str(frames_dir / 'frame_%06d.png')]
    run_command(cmd)

    frames = list(frames_dir.glob('frame_*.png'))
    frames.sort(key=lambda x: int(x.stem.replace('frame_', '')))
    print(f"  Extracted {len(frames)} frames to {frames_dir}")
    return frames


def vpx_speed_to_int(preset: str) -> int:
    """Map a named VP9 speed preset to a libvpx ``-cpu-used`` value.

    Args:
        preset: One of the keys of :data:`VPX_PRESET_SPEEDS`.

    Returns:
        The ``-cpu-used`` value, 0 (slowest, best) to 8 (fastest).
        Unknown names map to 4.
    """
    return VPX_PRESET_SPEEDS.get(preset, _DEFAULT_VPX_SPEED)


def _has_encoder(name: str) -> bool:
    """Report whether the local ffmpeg build lists the named encoder."""
    try:
        check = subprocess.run(['ffmpeg', '-encoders'], capture_output=True,
                               text=True, timeout=5)
        return name in check.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _webm_audio_args() -> list[str]:
    """Build the audio mapping for WebM: Opus when available, otherwise Vorbis."""
    if _has_encoder('libopus'):
        codec = 'libopus'
    else:
        print("  Warning: libopus unavailable, using Vorbis for audio")
        codec = 'libvorbis'
    return ['-map', '1:a', '-c:a', codec, '-b:a', '128k', '-shortest']


def _scale_args(first_frame: StrPath, target_width: int, target_height: int) -> list[str]:
    """Build the scale filter args, or ``[]`` when the frame already matches.

    Scales whenever the frame size cannot be determined, since scaling a
    frame that is already the right size is harmless.
    """
    scale = ['-vf', f'scale={target_width}:{target_height}:flags=lanczos']
    if not Path(first_frame).exists():
        return scale

    from PIL import Image
    try:
        with Image.open(first_frame) as frame:
            fw, fh = frame.size
    except Exception as e:
        print(f"  Warning: Could not probe frame size: {e}")
        return scale

    if fw != target_width or fh != target_height:
        print(f"  Scaling: {fw}x{fh} -> {target_width}x{target_height}")
        return scale
    print(f"  Frames at target: {fw}x{fh}")
    return []


def _vp9_alpha_args(vpx_preset: str) -> list[str]:
    """Build the VP9-with-alpha encoder settings.

    Kept deliberately minimal: richer libvpx parameter sets have been
    seen to hang the encoder on RGBA input.
    """
    speed_val = vpx_speed_to_int(vpx_preset)
    print(f"  VP9 preset: {vpx_preset} (cpu-used={speed_val})")
    return ['-c:v', 'libvpx-vp9',
            '-crf', '20',
            '-b:v', '0',
            '-cpu-used', str(speed_val),
            '-row-mt', '1',
            '-pix_fmt', 'yuva420p',  # safe here because input is forced to rgba
            '-deadline', 'realtime']


def assemble_video(rendered_images: Sequence[StrPath], output_path: StrPath, fps: float,
                   source_video: StrPath | None, has_audio: bool,
                   target_width: int, target_height: int,
                   check_frames: bool = True,
                   bg_alpha: float = DEFAULT['BACKGROUND-ALPHA'],
                   vpx_preset: str = DEFAULT['VPX-PRESET']) -> StrPath:
    """Encode rendered frames, plus the source audio, into the final video.

    An opaque background produces H.264/AAC (MP4). ``bg_alpha`` below 1
    produces VP9 with an alpha channel and Opus audio (WebM), falling
    back to Vorbis if the ffmpeg build lacks ``libopus``. The container
    is chosen by ffmpeg from the extension of ``output_path``, so the
    caller must pass a ``.webm`` name for alpha output.

    Args:
        rendered_images: Rendered frame paths. All files matching
            ``rendered_*.png`` in the directory of the first entry are
            encoded, in name order; must not be empty.
        output_path: Video file to write.
        fps: Output frame rate.
        source_video: Video to take audio from, or ``None``.
        has_audio: Whether ``source_video`` has an audio stream. Audio
            is muxed only if this is set and the file exists.
        target_width: Output width in pixels.
        target_height: Output height in pixels.
        check_frames: Compare the first frame against the target size
            and add a scale filter if they differ. Leave off when the
            frames were just rendered at the target size.
        bg_alpha: Background opacity the frames were rendered with;
            selects the codec as described above.
        vpx_preset: VP9 speed preset; see :func:`vpx_speed_to_int`.

    Returns:
        ``output_path``.

    Raises:
        RuntimeError: If ``ffmpeg`` fails.
        IndexError: If ``rendered_images`` is empty.
    """
    print("  Assembling video...")
    pattern = str(Path(rendered_images[0]).parent / 'rendered_*.png')
    use_alpha = bg_alpha < 1.0
    mux_audio = bool(has_audio and source_video and Path(source_video).exists())

    cmd = ['ffmpeg', '-stats', '-y']
    if use_alpha:
        print("  Output: WebM (VP9 + alpha)")
        print("  Note: Using -pix_fmt at input to prevent encoder hang")
        # Critical: force the PNG decoder to output RGBA at INPUT
        cmd += ['-pix_fmt', 'rgba']
    else:
        print("  Output: MP4 (H.264)")
    cmd += ['-framerate', f'{fps:.6f}', '-pattern_type', 'glob', '-i', pattern]

    if mux_audio:
        cmd += ['-i', str(source_video)]
        if use_alpha:
            cmd += _webm_audio_args()

    if check_frames:
        cmd += _scale_args(rendered_images[0], target_width, target_height)

    cmd += ['-map', '0:v']
    if use_alpha:
        cmd += _vp9_alpha_args(vpx_preset)
    else:
        if mux_audio:
            cmd += ['-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-shortest']
        cmd += ['-c:v', 'libx264', '-crf', '18',
                '-preset', 'medium', '-pix_fmt', 'yuv420p']
    cmd += [str(output_path)]

    print("  Executing ffmpeg...")
    run_command(cmd, check=True)
    return output_path
