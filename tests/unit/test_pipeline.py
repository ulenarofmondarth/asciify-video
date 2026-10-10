"""Programmatic entry point: run_conversion()."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from asciify_video import config, pipeline, state
from tests.conftest import FAKE_FRAME_COUNT, FAKE_FRAME_HEIGHT, FAKE_SOURCE
from tests.helpers import isolated_state


class TestRunConversion:
    def test_raises_instead_of_exiting(self, monkeypatch, tmp_path):
        """Core is library-grade: failures propagate as exceptions, and
        the CLI is responsible for converting them to exit codes."""
        isolated_state(monkeypatch, tmp_path)
        monkeypatch.setattr(
            pipeline, 'probe_video',
            MagicMock(side_effect=RuntimeError('HALT_AFTER_PREFLIGHT')))
        cfg = config.ConversionConfig(input=tmp_path / 'in.mp4',
                                       output=tmp_path / 'out.mp4')
        with pytest.raises(RuntimeError, match='HALT_AFTER_PREFLIGHT'):
            pipeline.run_conversion(cfg)

    def test_cleanup_on_failure_without_keep_temp(self, monkeypatch,
                                                  tmp_path):
        """A failing run must not leak its temp dir (unless keep_temp)."""
        isolated_state(monkeypatch, tmp_path)
        real_mkdtemp = pipeline.tempfile.mkdtemp

        seen = []

        def tracking_mkdtemp(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            seen.append(d)
            return d
        monkeypatch.setattr(pipeline.tempfile, 'mkdtemp', tracking_mkdtemp)
        monkeypatch.setattr(
            pipeline, 'probe_video',
            MagicMock(side_effect=RuntimeError('boom')))
        cfg = config.ConversionConfig(input=tmp_path / 'in.mp4',
                                       output=tmp_path / 'out.mp4')
        with pytest.raises(RuntimeError, match='boom'):
            pipeline.run_conversion(cfg)
        for d in seen:
            assert not Path(d).is_dir()


ALL_STAGES = ['probe', 'key', 'extract', 'ascii', 'render', 'assemble']


def make_cfg(tmp_path, **overrides):
    options = dict(input=tmp_path / 'in.mp4', output=tmp_path / 'out.mp4',
                   jobs=1, silent_progress=True, ascii_width=16)
    options.update(overrides)
    return config.ConversionConfig(**options)


@pytest.fixture
def kept_run(fake_ffmpeg, tmp_path):
    """A finished --keep-temp run; yields its temp dir for reuse tests."""
    pipeline.run_conversion(make_cfg(tmp_path, keep_temp=True))
    temp_dir = Path(state.load_state()['temp_dir'])
    for mock in (fake_ffmpeg.probe, fake_ffmpeg.sample, fake_ffmpeg.chromakey,
                 fake_ffmpeg.extract, fake_ffmpeg.assemble):
        mock.reset_mock()
    fake_ffmpeg.rendered_sizes.clear()
    return temp_dir


class TestFreshRun:
    def test_runs_every_stage_and_records_state(self, fake_ffmpeg, tmp_path):
        cfg = make_cfg(tmp_path)
        assert pipeline.run_conversion(cfg) == cfg.output

        recorded = state.load_state()
        assert recorded['completed'] is True
        assert recorded['stages_completed'] == ALL_STAGES
        assert recorded['output'] == str(cfg.output)
        assert recorded['temp_dir'] is None
        assert recorded['probe'] == FAKE_SOURCE
        assert recorded['key']['mode'] == 'auto'
        assert recorded['key']['color'] == '0x10C020'
        assert recorded['grid'] == {'ascii_width': 16, 'rows': FAKE_FRAME_HEIGHT // 2,
                                    'stretch': None, 'ramp': 'default'}
        assert recorded['counts'] == {'frames': FAKE_FRAME_COUNT,
                                      'ascii_files': FAKE_FRAME_COUNT,
                                      'rendered_images': FAKE_FRAME_COUNT}

    def test_stage_calls_are_wired_together(self, fake_ffmpeg, tmp_path):
        cfg = make_cfg(tmp_path)
        pipeline.run_conversion(cfg)

        keyed = fake_ffmpeg.chromakey.call_args.args[1]
        assert fake_ffmpeg.chromakey.call_args.args[2] == '0x10C020'
        assert fake_ffmpeg.extract.call_args.args[0] == keyed
        assert fake_ffmpeg.extract.call_args.args[2:] == (25.0, 16)

        rendered, output, fps, source, has_audio, tw, th = \
            fake_ffmpeg.assemble.call_args.args
        assert len(rendered) == FAKE_FRAME_COUNT
        assert (output, fps, source, has_audio) == (cfg.output, 25.0, cfg.input, True)
        assert (tw, th) == (320, 160)
        assert fake_ffmpeg.assemble.call_args.kwargs['check_frames'] is False
        assert fake_ffmpeg.rendered_sizes == {(320, 160)}

    def test_temp_dir_removed_after_success(self, fake_ffmpeg, tmp_path):
        pipeline.run_conversion(make_cfg(tmp_path))
        keyed = fake_ffmpeg.chromakey.call_args.args[1]
        assert not keyed.parent.exists()

    def test_no_key_skips_keying_and_extracts_from_input(self, fake_ffmpeg, tmp_path):
        cfg = make_cfg(tmp_path, no_key=True)
        pipeline.run_conversion(cfg)
        fake_ffmpeg.sample.assert_not_called()
        fake_ffmpeg.chromakey.assert_not_called()
        assert fake_ffmpeg.extract.call_args.args[0] == cfg.input
        assert state.load_state()['key'] == {
            'mode': 'none', 'color': None, 'detected': False,
            'tolerance': cfg.tolerance, 'edge_blending': cfg.edge_blending}

    def test_manual_key_color_skips_detection(self, fake_ffmpeg, tmp_path):
        pipeline.run_conversion(make_cfg(tmp_path, key_color='0x00B140'))
        fake_ffmpeg.sample.assert_not_called()
        assert fake_ffmpeg.chromakey.call_args.args[2] == '0x00B140'
        assert state.load_state()['key']['mode'] == 'manual'

    def test_detect_green_overrides_manual_key_color(self, fake_ffmpeg, tmp_path):
        pipeline.run_conversion(
            make_cfg(tmp_path, key_color='0x00B140', detect_green=True))
        assert fake_ffmpeg.chromakey.call_args.args[2] == '0x10C020'

    def test_failed_detection_recorded_as_manual(self, fake_ffmpeg, tmp_path):
        fake_ffmpeg.sample.return_value = ('0x00FF00', False)
        pipeline.run_conversion(make_cfg(tmp_path))
        assert state.load_state()['key']['mode'] == 'manual'

    def test_progress_setting_reaches_key_sampling(self, fake_ffmpeg, tmp_path):
        pipeline.run_conversion(make_cfg(tmp_path, silent_progress=True))
        assert fake_ffmpeg.sample.call_args.kwargs['show_progress'] is False

    def test_fps_override_and_target_resolution(self, fake_ffmpeg, tmp_path):
        pipeline.run_conversion(
            make_cfg(tmp_path, fps_override=12.5, target_resolution=(200, 120)))
        args = fake_ffmpeg.assemble.call_args.args
        assert args[2] == 12.5 and args[5:7] == (200, 120)
        assert fake_ffmpeg.extract.call_args.args[2] == 12.5
        assert fake_ffmpeg.rendered_sizes == {(200, 120)}
        assert state.load_state()['probe']['fps'] == 12.5

    @pytest.mark.parametrize('ramp, recorded_as', [
        (None, 'default'), ('jp2a', 'jp2a'), ('.:#', '.:#')])
    def test_ramp_resolution(self, fake_ffmpeg, tmp_path, ramp, recorded_as):
        pipeline.run_conversion(make_cfg(tmp_path, ramp=ramp))
        assert state.load_state()['grid']['ramp'] == recorded_as

    def test_hist_stretch_records_bounds(self, fake_ffmpeg, tmp_path, capsys):
        pipeline.run_conversion(make_cfg(tmp_path, hist_stretch=True))
        lo, hi = state.load_state()['grid']['stretch']
        assert 0 <= lo < hi <= 255
        assert f'Stretch range: {lo}-{hi}' in capsys.readouterr().out

    def test_no_frames_is_an_error(self, fake_ffmpeg, tmp_path):
        fake_ffmpeg.extract.side_effect = None
        fake_ffmpeg.extract.return_value = []
        with pytest.raises(RuntimeError, match='No frames'):
            pipeline.run_conversion(make_cfg(tmp_path))
        fake_ffmpeg.assemble.assert_not_called()
        assert state.load_state()['completed'] is False

    def test_interrupt_saves_state_and_propagates(self, fake_ffmpeg, tmp_path, capsys):
        fake_ffmpeg.extract.side_effect = KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            pipeline.run_conversion(make_cfg(tmp_path))
        assert 'Interrupted by user' in capsys.readouterr().out
        assert state.load_state()['stages_completed'] == ['probe', 'key']


class TestKeepTemp:
    def test_artifacts_kept_and_recorded(self, kept_run):
        assert state.load_state()['temp_dir'] == str(kept_run)
        for sub in ('frames', 'txt_frames', 'images'):
            assert len(list((kept_run / sub).iterdir())) == FAKE_FRAME_COUNT
        text = (kept_run / 'txt_frames' / 'frame_000000.txt').read_text()
        assert [len(line) for line in text.split('\n')] == [16] * (FAKE_FRAME_HEIGHT // 2)

    def test_temp_dir_kept_on_failure(self, fake_ffmpeg, tmp_path):
        fake_ffmpeg.assemble.side_effect = RuntimeError('encode failed')
        with pytest.raises(RuntimeError, match='encode failed'):
            pipeline.run_conversion(make_cfg(tmp_path, keep_temp=True))
        assert fake_ffmpeg.extract.call_args.args[1].is_dir()


class TestReuse:
    def reuse_cfg(self, tmp_path, kept_run, level, **overrides):
        return make_cfg(tmp_path, reuse_level=level, reuse_dir=kept_run,
                        output=tmp_path / 'again.mp4', **overrides)

    @pytest.mark.parametrize('level', ['extracted', 'ascii', 'final'])
    def test_keying_and_extraction_skipped(self, fake_ffmpeg, tmp_path, kept_run, level):
        pipeline.run_conversion(self.reuse_cfg(tmp_path, kept_run, level))
        fake_ffmpeg.sample.assert_not_called()
        fake_ffmpeg.chromakey.assert_not_called()
        fake_ffmpeg.extract.assert_not_called()
        recorded = state.load_state()
        assert recorded['stages_completed'] == ALL_STAGES
        assert recorded['key']['mode'] == 'none'
        assert recorded['counts']['rendered_images'] == FAKE_FRAME_COUNT

    def test_reused_dir_is_never_deleted(self, fake_ffmpeg, tmp_path, kept_run):
        pipeline.run_conversion(self.reuse_cfg(tmp_path, kept_run, 'extracted'))
        assert (kept_run / 'frames').is_dir()

    def test_extracted_honours_new_grid_width(self, fake_ffmpeg, tmp_path, kept_run):
        pipeline.run_conversion(
            self.reuse_cfg(tmp_path, kept_run, 'extracted', ascii_width=8))
        assert state.load_state()['grid']['ascii_width'] == 8

    def test_ascii_takes_grid_from_text_files(self, fake_ffmpeg, tmp_path, kept_run,
                                              capsys):
        pipeline.run_conversion(self.reuse_cfg(
            tmp_path, kept_run, 'ascii', ascii_width=8, hist_stretch=True,
            target_resolution=(640, 320)))
        out = capsys.readouterr().out
        assert 'reused ASCII grid is 16 cols; --ascii-width=8 ignored' in out
        assert '--hist-stretch affects conversion; ignored' in out
        grid = state.load_state()['grid']
        assert (grid['ascii_width'], grid['stretch']) == (16, None)
        assert fake_ffmpeg.rendered_sizes == {(640, 320)}

    def test_ascii_frame_count_mismatch(self, fake_ffmpeg, tmp_path, kept_run):
        (kept_run / 'txt_frames' / 'frame_000000.txt').unlink()
        with pytest.raises(RuntimeError, match='ascii/frame count mismatch: 2 txt vs 3'):
            pipeline.run_conversion(self.reuse_cfg(tmp_path, kept_run, 'ascii'))

    def test_final_reuses_rendered_frames_and_checks_size(self, fake_ffmpeg, tmp_path,
                                                          kept_run, capsys):
        pipeline.run_conversion(self.reuse_cfg(
            tmp_path, kept_run, 'final', target_resolution=(640, 320)))
        rendered = fake_ffmpeg.assemble.call_args.args[0]
        assert {Path(p).parent for p in rendered} == {kept_run / 'images'}
        assert fake_ffmpeg.assemble.call_args.kwargs['check_frames'] is True
        assert 'reused frames are 320x160, target is 640x320' in capsys.readouterr().out
        assert state.load_state()['grid']['ascii_width'] is None


class TestGridWidth:
    """--scale-divisor derives the grid width when --ascii-width is absent."""

    def grid_width(self, fake_ffmpeg, tmp_path, **overrides):
        pipeline.run_conversion(make_cfg(tmp_path, **overrides))
        extracted_at = fake_ffmpeg.extract.call_args.args[3]
        assert state.load_state()['grid']['ascii_width'] == extracted_at
        return extracted_at

    def test_default_is_source_width_over_eight(self, fake_ffmpeg, tmp_path):
        assert self.grid_width(fake_ffmpeg, tmp_path, ascii_width=None) == 320 // 8

    def test_divisor_is_honoured(self, fake_ffmpeg, tmp_path, capsys):
        assert self.grid_width(fake_ffmpeg, tmp_path, ascii_width=None,
                               scale_divisor=4) == 80
        assert 'Extracting at 80px wide for 1:1 character mapping ' \
               '(output width / --scale-divisor 4)' in capsys.readouterr().out

    def test_explicit_width_wins_over_divisor(self, fake_ffmpeg, tmp_path, capsys):
        assert self.grid_width(fake_ffmpeg, tmp_path, ascii_width=50,
                               scale_divisor=4) == 50
        assert '--scale-divisor' not in capsys.readouterr().out

    @pytest.mark.parametrize('source_width', [320, 1920])
    def test_same_target_resolution_gives_same_grid_for_any_source(
            self, fake_ffmpeg, tmp_path, source_width):
        """README guarantee: identical character dimensions at one target size."""
        fake_ffmpeg.probe.return_value = dict(FAKE_SOURCE, width=source_width)
        assert self.grid_width(fake_ffmpeg, tmp_path, ascii_width=None,
                               target_resolution=(640, 360)) == 80
        assert fake_ffmpeg.rendered_sizes == {(640, 360)}

    def test_width_never_drops_below_one_column(self, fake_ffmpeg, tmp_path):
        fake_ffmpeg.probe.return_value = dict(FAKE_SOURCE, width=4)
        assert self.grid_width(fake_ffmpeg, tmp_path, ascii_width=None) == 1

    def test_reused_ascii_ignores_divisor_silently(self, fake_ffmpeg, tmp_path,
                                                   kept_run, capsys):
        pipeline.run_conversion(make_cfg(
            tmp_path, reuse_level='ascii', reuse_dir=kept_run,
            ascii_width=None, scale_divisor=2))
        assert state.load_state()['grid']['ascii_width'] == 16
        assert 'ignored' not in capsys.readouterr().out


class TestProbeRestore:
    """A reuse run takes source properties from the state file, not ffprobe."""

    def reuse(self, tmp_path, kept_run, level='ascii', **overrides):
        return pipeline.run_conversion(make_cfg(
            tmp_path, reuse_level=level, reuse_dir=kept_run, **overrides))

    def test_probe_is_restored_not_rerun(self, fake_ffmpeg, tmp_path, kept_run, capsys):
        self.reuse(tmp_path, kept_run)
        fake_ffmpeg.probe.assert_not_called()
        assert 'Restored from state file: 320x160 @ 25.00 fps, audio=yes' \
            in capsys.readouterr().out
        assert state.load_state()['probe'] == FAKE_SOURCE

    def test_reused_dir_stays_recorded_for_the_next_reuse(self, fake_ffmpeg, tmp_path,
                                                          kept_run):
        for level in ('extracted', 'ascii', 'final'):
            self.reuse(tmp_path, kept_run, level)
            assert state.load_state()['temp_dir'] == str(kept_run)
        fake_ffmpeg.probe.assert_not_called()

    def test_reused_dir_stays_recorded_after_a_failed_run(self, fake_ffmpeg, tmp_path,
                                                          kept_run):
        fake_ffmpeg.assemble.side_effect = RuntimeError('encode failed')
        with pytest.raises(RuntimeError):
            self.reuse(tmp_path, kept_run)
        assert state.load_state()['temp_dir'] == str(kept_run)

    def test_restored_fps_is_the_rate_frames_were_extracted_at(self, fake_ffmpeg,
                                                               tmp_path):
        pipeline.run_conversion(make_cfg(tmp_path, keep_temp=True, fps_override=12.5))
        kept = Path(state.load_state()['temp_dir'])
        self.reuse(tmp_path, kept, 'final')
        assert fake_ffmpeg.assemble.call_args.args[2] == 12.5

    def test_fps_override_still_applies_on_top_of_restore(self, fake_ffmpeg, tmp_path,
                                                          kept_run):
        self.reuse(tmp_path, kept_run, 'final', fps_override=50.0)
        assert fake_ffmpeg.assemble.call_args.args[2] == 50.0

    def test_state_for_a_different_dir_is_not_trusted(self, fake_ffmpeg, tmp_path,
                                                      kept_run):
        other = tmp_path / 'copied'
        shutil.copytree(kept_run, other)
        self.reuse(tmp_path, other)
        fake_ffmpeg.probe.assert_called_once()

    def test_incomplete_recorded_probe_is_not_trusted(self, fake_ffmpeg, tmp_path,
                                                      kept_run):
        recorded = state.load_state()
        del recorded['probe']['fps']
        state.save_state(recorded)
        self.reuse(tmp_path, kept_run)
        fake_ffmpeg.probe.assert_called_once()
