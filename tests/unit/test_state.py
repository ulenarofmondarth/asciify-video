"""State file handling."""

import json

from asciify_video import (
    state,
)
from tests.helpers import isolated_state


class TestStateFile:
    def test_save_load_roundtrip(self, monkeypatch, tmp_path):
        isolated_state(monkeypatch, tmp_path)
        state.save_state({'version': 1, 'input': 'in.mp4',
                            'completed': True})
        loaded = state.load_state()
        assert loaded['input'] == 'in.mp4'
        assert loaded['completed'] is True
        assert loaded['updated'] is not None  # stamped on save

    def test_missing_defaults(self, monkeypatch, tmp_path):
        isolated_state(monkeypatch, tmp_path)
        loaded = state.load_state()
        for key in ('version', 'updated', 'input', 'temp_dir', 'keep_temp',
                    'completed', 'stages_completed', 'probe', 'key', 'grid',
                    'render', 'counts', 'output', 'duration_s'):
            assert key in loaded

    def test_corrupt_json_falls_back(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text('{not json')
        loaded = state.load_state()
        assert loaded['version'] == 1
        assert 'could not parse' in capsys.readouterr().out

    def test_partial_state_gets_template_fields(self, monkeypatch, tmp_path):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps({'input': 'x.mp4', 'bogus_future_key': 7}))
        loaded = state.load_state()
        assert loaded['input'] == 'x.mp4'
        assert 'completed' in loaded          # defaulted
        assert loaded['bogus_future_key'] == 7  # forward-compatible

    def test_count_dir(self, tmp_path):
        assert state.count_dir(None) == 0
        assert state.count_dir(tmp_path / 'nope') == 0
        (tmp_path / 'a').mkdir()
        for i in range(5):
            (tmp_path / 'a' / f'f{i}').touch()
        assert state.count_dir(tmp_path / 'a') == 5


class TestReportState:
    FULL_STATE = {
        'version': 1, 'updated': '2026-10-06T14:22:31',
        'input': 'input.mp4', 'temp_dir': None, 'keep_temp': False,
        'completed': True,
        'stages_completed': ['probe', 'key', 'extract', 'ascii',
                              'render', 'assemble'],
        'probe': {'width': 1920, 'height': 1080, 'fps': 24.0,
                  'has_audio': True},
        'key': {'mode': 'auto', 'color': '0x00B140', 'detected': True,
                'tolerance': 0.25, 'edge_blending': 0.05},
        'grid': {'ascii_width': 160, 'rows': 135, 'stretch': None,
                 'ramp': 'default'},
        'render': {'target_width': 1920, 'target_height': 1080,
                   'font_size': 64, 'font_color': '#FFB000',
                   'colorized': False, 'leave_black_empty': False,
                   'black_threshold': 50, 'bg_color': '#000000',
                   'bg_alpha': 1.0},
        'counts': {'frames': 108, 'ascii_files': 108,
                   'rendered_images': 108},
        'output': 'output.mp4', 'duration_s': 127.4,
    }

    def test_full_report(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps(self.FULL_STATE))
        state.report_state(state.load_state())
        out = capsys.readouterr().out
        for fragment in ('1920x1080 @ 24.00 fps', 'audio=yes',
                         '0x00B140 (auto-detected)', 'tol=0.25',
                         '160 cols x 135 rows', 'stretch=off',
                         'font=64', 'color=#FFB000',
                         'alpha=1.00', 'frames=108', 'Last output',
                         'No reusable temp dir'):
            assert fragment in out, fragment

    def test_keyless_report(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps(
            {'key': {'mode': 'none'}, 'stages_completed': []}))
        state.report_state(state.load_state())
        assert '(--no-key)' in capsys.readouterr().out
