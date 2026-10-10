"""Green key-colour sampling (ffmpeg rawvideo output faked)."""

import json
from unittest.mock import MagicMock

import pytest

from asciify_video import (
    keying,
)


class TestSampleGreenColor:
    RAW_W = 160
    RAW_H = 90

    def _setup(self, monkeypatch, tmp_path, pixels_fn, height=None):
        height = height or self.RAW_H
        probe = MagicMock(stdout=json.dumps({'format': {'duration': '10'}}))
        monkeypatch.setattr(keying, 'run_command',
                            MagicMock(return_value=probe))

        def fake_run(cmd, capture_output=True):
            w, h = self.RAW_W, height
            buf = bytearray(w * h * 3)
            for py in range(h):
                for px in range(w):
                    buf[(py * w + px) * 3:(py * w + px) * 3 + 3] = \
                        bytes(pixels_fn(px, py))
            return MagicMock(returncode=0, stdout=bytes(buf))
        monkeypatch.setattr(keying.subprocess, 'run', fake_run)
        return tmp_path / 'in.mp4'

    def _corner_mask(self, px, py, w=RAW_W, h=RAW_H):
        bw, bh = w // 6, h // 6
        return (px < bw or px >= w - bw) and (py < bh or py >= h - bh)

    def test_detects_corner_green(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (0, 255, 0) if self._corner_mask(px, py) else (120, 120, 120)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = keying.sample_green_color(
            path, sample_frames=3, region='corners')
        assert detected is True
        assert color == '0x00FF00'

    def test_corners_excluded_when_sampling_center(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (0, 255, 0) if self._corner_mask(px, py) else (10, 10, 10)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = keying.sample_green_color(
            path, sample_frames=3, region='center')
        assert detected is False
        assert color == '0x00FF00'  # documented fallback

    def test_brightness_gate_rejects_extremes(self, monkeypatch, tmp_path):
        # Green-dominant but average brightness 223.7 > 220: filtered by
        # the brightness gate, so no detection occurs
        def px_fn(px, py):
            return (210, 251, 210) if self._corner_mask(px, py) else (0, 0, 0)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = keying.sample_green_color(
            path, sample_frames=2, region='corners')
        assert detected is False
        assert color == '0x00FF00'

    def test_avg_color_not_pure_green(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (40, 200, 60) if self._corner_mask(px, py) else (5, 5, 5)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = keying.sample_green_color(
            path, sample_frames=2, region='corners')
        assert detected is True
        assert color == '0x28C83C'

    def test_exception_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(keying, 'run_command',
                            MagicMock(side_effect=RuntimeError('boom')))
        color, detected = keying.sample_green_color(tmp_path / 'x.mp4')
        assert (color, detected) == ('0x00FF00', False)


class TestRegionPredicate:
    FW, FH = 160, 90

    def predicate(self, region, src=(1600, 900)):
        return keying._region_predicate(region, self.FW, self.FH, *src)

    def test_full_accepts_everything(self):
        assert self.predicate('full')(0, 0) and self.predicate('full')(159, 89)

    def test_center_is_middle_third(self):
        inside = self.predicate('center')
        assert inside(80, 45)
        assert not inside(10, 45) and not inside(80, 5)

    def test_corners_are_outer_sixths_on_both_axes(self):
        inside = self.predicate('corners')
        assert inside(0, 0) and inside(159, 89) and inside(0, 89)
        assert not inside(80, 0) and not inside(0, 45)

    def test_crop_spec_is_in_source_pixels(self):
        inside = self.predicate('400x300+1000+500')   # x 1000-1399, y 500-799
        assert inside(100, 50) and inside(139, 79)
        assert not inside(99, 50) and not inside(140, 50) and not inside(100, 80)

    @pytest.mark.parametrize('spec', ['bogus', '10x10', '10x10+5', 'x10+0+0'])
    def test_invalid_spec_raises(self, spec):
        with pytest.raises(ValueError, match='Invalid region spec'):
            self.predicate(spec)


class TestSamplingFallbacks:
    def test_invalid_region_falls_back_instead_of_raising(self, monkeypatch, tmp_path,
                                                          capsys):
        probe = MagicMock(stdout=json.dumps({'format': {'duration': '10'}}))
        monkeypatch.setattr(keying, 'run_command', MagicMock(return_value=probe))
        monkeypatch.setattr(keying.subprocess, 'run', lambda *a, **k: MagicMock(
            returncode=0, stdout=bytes([20, 200, 30]) * 160 * 4))
        result = keying.sample_green_color(tmp_path / 'in.mp4', region='bogus',
                                           show_progress=False)
        assert result == (keying.FALLBACK_KEY_COLOR, False)
        assert 'Invalid region spec: bogus' in capsys.readouterr().out

    @pytest.mark.parametrize('returncode, stdout', [
        (1, bytes(160 * 3)),         # ffmpeg failed
        (0, b''),                    # no data
        (0, bytes(30)),              # less than one row
    ])
    def test_unusable_frames_are_skipped(self, monkeypatch, tmp_path, capsys,
                                         returncode, stdout):
        probe = MagicMock(stdout=json.dumps({'format': {'duration': '10'}}))
        monkeypatch.setattr(keying, 'run_command', MagicMock(return_value=probe))
        monkeypatch.setattr(keying.subprocess, 'run', lambda *a, **k: MagicMock(
            returncode=returncode, stdout=stdout))
        result = keying.sample_green_color(tmp_path / 'in.mp4', sample_frames=2,
                                           show_progress=False)
        assert result == (keying.FALLBACK_KEY_COLOR, False)
        assert 'Could not detect strong green' in capsys.readouterr().out
