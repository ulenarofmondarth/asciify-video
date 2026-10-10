"""End-to-end conversion through the real ffmpeg and ffprobe binaries.

Skipped automatically when the binaries are not on PATH.
"""

import shutil
import subprocess

import pytest

from asciify_video import cli, ffmpeg, state

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
        reason='ffmpeg and ffprobe are required on PATH'),
]

WIDTH, HEIGHT, FPS, SECONDS = 160, 90, 10, 1


@pytest.fixture(scope='module')
def green_screen_clip(tmp_path_factory):
    """A one-second clip: white box on chroma green, with a sine-wave soundtrack."""
    path = tmp_path_factory.mktemp('clip') / 'green.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', f'color=c=0x00FF00:s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}',
        '-f', 'lavfi', '-i', f'sine=frequency=440:duration={SECONDS}',
        '-vf', 'drawbox=x=50:y=25:w=60:h=40:color=white:t=fill',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
        str(path)], check=True)
    return path


def has_encoder(name):
    listing = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                             capture_output=True, text=True).stdout
    return name in listing


def convert(clip, output, *extra, width=('--ascii-width', '40')):
    return cli.main([str(clip), str(output), *width, '--jobs', '1',
                     '--silent-progress', *extra])


def test_green_screen_clip_converts_to_mp4(green_screen_clip, tmp_path):
    output = tmp_path / 'ascii.mp4'
    assert convert(green_screen_clip, output) == 0

    info = ffmpeg.probe_video(output)
    assert (info['width'], info['height']) == (WIDTH, HEIGHT)
    assert info['fps'] == pytest.approx(FPS)
    assert info['has_audio'] is True

    recorded = state.load_state()
    assert recorded['completed'] is True
    assert recorded['key']['mode'] == 'auto'
    assert recorded['counts']['frames'] == FPS * SECONDS
    assert recorded['temp_dir'] is None


def test_keep_temp_then_reuse_each_level(green_screen_clip, tmp_path):
    assert convert(green_screen_clip, tmp_path / 'first.mp4', '--keep-temp') == 0
    kept = state.load_state()['temp_dir']
    try:
        for level in ('extracted', 'ascii', 'final'):
            output = tmp_path / f'{level}.mp4'
            assert convert(green_screen_clip, output,
                           '--reuse', level, '--reuse-dir', kept) == 0
            assert ffmpeg.probe_video(output)['width'] == WIDTH
    finally:
        shutil.rmtree(kept, ignore_errors=True)


def test_transparent_background_produces_webm(green_screen_clip, tmp_path):
    if not has_encoder('libvpx-vp9'):
        pytest.skip('ffmpeg build lacks libvpx-vp9')
    assert convert(green_screen_clip, tmp_path / 'alpha.mp4',
                   '--background-alpha', '0') == 0
    output = tmp_path / 'alpha.webm'          # renamed by the WebM rule
    assert output.exists() and not (tmp_path / 'alpha.mp4').exists()
    assert ffmpeg.probe_video(output)['width'] == WIDTH


def test_scale_divisor_sets_grid_width(green_screen_clip, tmp_path):
    assert convert(green_screen_clip, tmp_path / 'out.mp4',
                   width=('--scale-divisor', '4')) == 0
    assert state.load_state()['grid']['ascii_width'] == WIDTH // 4
