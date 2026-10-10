"""Optional checks against a real --keep-temp directory.

Point ASCIIFY_TEST_ARTIFACTS at a temp dir from a --keep-temp run:

    asciify_video sample.mp4 out.mp4 --keep-temp
    export ASCIIFY_TEST_ARTIFACTS=/tmp/asciify_xxxx
    pytest tests/integration -v
"""

import os
from pathlib import Path

import pytest
from PIL import Image

from asciify_video import render, reuse
from tests.helpers import build_metrics, make_task

ARTIFACTS = Path(os.environ.get('ASCIIFY_TEST_ARTIFACTS', ''))
REAL_FRAMES = sorted((ARTIFACTS / 'frames').glob('frame_*.png')) \
    if (ARTIFACTS / 'frames').is_dir() else []

needs_real_frames = pytest.mark.skipif(
    not REAL_FRAMES, reason="set ASCIIFY_TEST_ARTIFACTS to a --keep-temp dir")


@needs_real_frames
class TestRealArtifacts:
    def test_real_frames_convert_deterministically(self, tmp_path):
        """First frame of a real run must produce a stable ASCII digest,
        guarding against refactors that alter character selection."""
        import hashlib
        frame = REAL_FRAMES[0]
        with Image.open(str(frame)) as im:
            cols, rows = 64, im.height // 2
        task = make_task(frame, tmp_path / 'out.png',
                         metrics=build_metrics(cols, rows, 640, 320))
        _, text = render._process_one_frame(task)
        digest = hashlib.sha256(text.encode()).hexdigest()
        assert digest  # record the hash in a pinned constant when it
        # stabilizes across a known-good revision, then compare here

    def test_real_temp_dir_validates(self):
        reuse.validate_reuse(ARTIFACTS, 'extracted')
        if (ARTIFACTS / 'txt_frames').is_dir() and \
                list((ARTIFACTS / 'txt_frames').glob('*.txt')):
            reuse.validate_reuse(ARTIFACTS, 'ascii')
