"""Suite-wide fixtures."""

import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from asciify_video import pipeline
from tests.helpers import isolated_state

FAKE_SOURCE = {'width': 320, 'height': 160, 'fps': 25.0, 'has_audio': True}
FAKE_FRAME_COUNT = 3
FAKE_FRAME_HEIGHT = 8


@pytest.fixture(autouse=True)
def _state_in_tmp(monkeypatch, tmp_path):
    """No test may read or write the developer's real ~/.cache/asciify state."""
    return isolated_state(monkeypatch, tmp_path)


def _write_fake_frames(video_path, frames_dir, target_fps=None, ascii_width=240):
    """Stand-in for ffmpeg.extract_frames: a few deterministic gradient PNGs."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(1, FAKE_FRAME_COUNT + 1):
        img = Image.new('RGB', (ascii_width, FAKE_FRAME_HEIGHT))
        img.putdata([((x * 7 + i * 40) % 256, (y * 30) % 256, 128)
                     for y in range(FAKE_FRAME_HEIGHT) for x in range(ascii_width)])
        path = frames_dir / f'frame_{i:06d}.png'
        img.save(path)
        frames.append(path)
    return frames


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    """Replace every ffmpeg-backed call run_conversion() makes.

    Conversion and rendering still run for real on the generated frames.
    The assemble mock records the size of the frames it was handed in
    ``rendered_sizes`` because the temp dir is gone once the run returns.
    Run temp dirs are created under tmp_path so nothing can leak.
    """
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    rendered_sizes = set()

    def record_assemble(rendered_images, output_path, *args, **kwargs):
        for path in rendered_images:
            with Image.open(path) as img:
                rendered_sizes.add(img.size)
        return output_path

    mocks = SimpleNamespace(
        probe=MagicMock(return_value=dict(FAKE_SOURCE)),
        sample=MagicMock(return_value=('0x10C020', True)),
        chromakey=MagicMock(side_effect=lambda src, dst, *a: dst),
        extract=MagicMock(side_effect=_write_fake_frames),
        assemble=MagicMock(side_effect=record_assemble),
        rendered_sizes=rendered_sizes,
    )
    monkeypatch.setattr(pipeline, 'probe_video', mocks.probe)
    monkeypatch.setattr(pipeline, 'sample_green_color', mocks.sample)
    monkeypatch.setattr(pipeline, 'chromakey_to_black', mocks.chromakey)
    monkeypatch.setattr(pipeline, 'extract_frames', mocks.extract)
    monkeypatch.setattr(pipeline, 'assemble_video', mocks.assemble)
    return mocks
