"""Reuse directory resolution and artifact validation."""

import shutil
from pathlib import Path

import pytest

from asciify_video import reuse
from tests.helpers import make_artifacts


class TestReuseHelpers:
    def test_resolve_priority_explicit_over_env_over_state(
            self, monkeypatch, tmp_path):
        explicit, envdir, statedir = (tmp_path / name for name in ('explicit', 'env', 'state'))
        for d in (explicit, envdir, statedir):
            d.mkdir()
        monkeypatch.setenv('ASCII_VIDEO_TEMP', str(envdir))
        recorded = {'temp_dir': str(statedir)}

        assert reuse.resolve_reuse_dir(str(explicit), recorded) == explicit
        assert reuse.resolve_reuse_dir(None, recorded) == envdir
        monkeypatch.delenv('ASCII_VIDEO_TEMP')
        assert reuse.resolve_reuse_dir(None, recorded) == statedir
        assert reuse.resolve_reuse_dir(None, {}) is None
        # nonexistent dirs yield None even when configured
        assert reuse.resolve_reuse_dir(
            str(tmp_path / 'ghost'), recorded) is None

    def test_validate_reuse_requirements(self, tmp_path):
        full = make_artifacts(tmp_path / 'full')
        reuse.validate_reuse(full, 'extracted')
        reuse.validate_reuse(full, 'ascii')
        reuse.validate_reuse(full, 'final')

        for level, missing in [('extracted', 'frames'),
                               ('ascii', 'txt_frames'),
                               ('final', 'images')]:
            partial = make_artifacts(tmp_path / f'partial_{level}')
            shutil.rmtree(partial / missing)
            with pytest.raises(RuntimeError, match=missing):
                reuse.validate_reuse(partial, level)

    def test_empty_dir_fails_validation(self, tmp_path):
        empty = make_artifacts(tmp_path / 'empty')
        (empty / 'frames').mkdir(exist_ok=True)
        for f in (empty / 'frames').iterdir():
            f.unlink()
        with pytest.raises(RuntimeError, match='empty'):
            reuse.validate_reuse(empty, 'extracted')

    def test_validate_frame_sizes_warns_on_mismatch(self, tmp_path, capsys):
        reuse_dir = make_artifacts(tmp_path / 'r')
        recorded = {'render': {'target_width': 320, 'target_height': 160}}
        assert reuse.validate_reuse_frame_sizes(
            reuse_dir, recorded, 640, 320) is True
        assert '640x320' in capsys.readouterr().out

    def test_validate_frame_sizes_silent_when_matching(self, tmp_path,
                                                       capsys):
        reuse_dir = make_artifacts(tmp_path / 'r')
        recorded = {'render': {'target_width': 64, 'target_height': 32}}
        reuse.validate_reuse_frame_sizes(reuse_dir, recorded, 64, 32)
        assert 'Warning' not in capsys.readouterr().out


class TestFrameNum:
    def test_frame_num_parsing(self):
        assert reuse.frame_num(Path('frame_000042.png')) == 42
        assert reuse.frame_num(Path('rendered_000108.png')) == 108
        assert reuse.frame_num(Path('no_digits.png')) == 0
