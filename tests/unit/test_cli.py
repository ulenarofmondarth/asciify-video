"""Command-line front end: parsing, validation, exit codes."""

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from asciify_video import (
    cli,
    pipeline,
)
from tests.helpers import call_main, invoke_main, isolated_state, make_artifacts


class TestReuseSemanticsReporting:
    """Dead-flag reporting lives in the CLI layer now — it inspects raw
    argv, which is inherently a command-line concern."""

    def test_dead_flag_warning(self, capsys):
        cli.report_reuse_semantics_cli(
            'final', ['in.mp4', 'out.mp4',
                      '--font-color', '#FF0000'])
        out = capsys.readouterr().out
        assert '!! --font-color was passed but is IGNORED (--reuse final)'\
            in out
        assert 'Still honored: --fps' in out

    def test_no_warning_when_flag_not_passed(self, capsys):
        cli.report_reuse_semantics_cli(
            'ascii', ['in.mp4', 'out.mp4'])
        assert '!!' not in capsys.readouterr().out

    def test_equal_sign_form_recognized(self, capsys):
        # --font-color=#FF0000 must count as passing --font-color
        cli.report_reuse_semantics_cli(
            'final', ['in.mp4', '--font-color=#FF0000'])
        assert 'IGNORED' in capsys.readouterr().out


class TestBuildConfig:
    def test_build_config_translates_args(self, monkeypatch, tmp_path):
        """CLI translation layer produces a fully normalized config."""
        parser = cli.build_parser()
        args = parser.parse_args([
            'in.mp4', 'out.mp4', '--background-color', '#102030',
            '--background-alpha', '0.5', '--font-color', '#AABBCC'])
        cfg = cli.build_config(args, Path('in.mp4'),
                                        Path('out.mp4'))
        assert cfg.background_rgb == (0x10, 0x20, 0x30)
        assert cfg.background_alpha == 0.5
        assert cfg.font_color_rgb == (0xAA, 0xBB, 0xCC)
        # WebM rename rule applied inside build_config
        assert cfg.output.name == 'out.webm'

    def test_target_resolution_parsed(self, monkeypatch, tmp_path):
        parser = cli.build_parser()
        args = parser.parse_args(
            ['in.mp4', 'out.mp4', '--target-resolution', '1280x720'])
        cfg = cli.build_config(args, Path('in.mp4'),
                                       Path('out.mp4'))
        assert cfg.target_resolution == (1280, 720)


class TestMainPreflight:
    def test_report_mode_succeeds(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps({'input': 'in.mp4', 'completed': True}))
        assert invoke_main(['--report']) == 0
        assert 'in.mp4' in capsys.readouterr().out

    def test_state_file_mode_dumps_json(self, monkeypatch, tmp_path, capsys):
        isolated_state(monkeypatch, tmp_path)
        assert invoke_main(['--state-file']) == 0
        assert json.loads(capsys.readouterr().out)['version'] == 1

    def test_missing_input_file(self, monkeypatch, tmp_path, capsys):
        isolated_state(monkeypatch, tmp_path)
        assert invoke_main(['/no/such/file.mp4', 'o.mp4']) == 1
        assert 'not found' in capsys.readouterr().out

    def test_missing_positional_args(self, monkeypatch, tmp_path):
        isolated_state(monkeypatch, tmp_path)
        # argparse's parser.error() exits with code 2
        with pytest.raises(SystemExit):
            cli.main([])

    def test_invalid_background_color(self, monkeypatch, tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4',
                          '--background-color', 'zzz'])
        assert code == 1
        assert 'Invalid background color' in capsys.readouterr().out

    def test_invalid_font_color(self, monkeypatch, tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--font-color', 'zzz'])
        assert code == 1
        assert 'Invalid font color' in capsys.readouterr().out

    def test_short_but_parseable_color_is_accepted(self, monkeypatch,
                                                    tmp_path, capsys):
        """Pins current (surprising) behavior: '#12345' has 5 hex digits;
        the 3rd component slices to '5' and parses, so it is accepted."""
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--font-color', '#12345'])
        assert code == 1  # halted at probe, i.e. NOT at color validation
        assert 'Invalid font color' not in capsys.readouterr().out

    def test_invalid_target_resolution(self, monkeypatch, tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4',
                          '--target-resolution', 'garbage'])
        assert code == 1
        assert 'Invalid target resolution' in capsys.readouterr().out

    def test_uncommon_extension_warns_but_continues(self, monkeypatch,
                                                    tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.dat', 'out.mp4'])
        assert code == 1  # past the warning, halted at probe
        out = capsys.readouterr().out
        assert 'Uncommon video format' in out

    def test_reuse_without_temp_dir_errors(self, monkeypatch, tmp_path,
                                            capsys):
        isolated_state(monkeypatch, tmp_path)   # no temp_dir recorded
        (tmp_path / 'in.mp4').write_bytes(b'')
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            pipeline, 'probe_video',
            MagicMock(side_effect=RuntimeError('should not reach probe')))
        code = invoke_main(['in.mp4', 'out.mp4', '--reuse', 'ascii'])
        assert code == 1
        assert 'no reusable temp directory' in capsys.readouterr().out


class TestWebMRenameBehavior:
    def test_alpha_renames_mp4_to_webm_with_notice(self, monkeypatch,
                                                    tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--background-alpha',
                          '0.5'])
        assert code == 1  # halted at probe, but after the rename logic
        out = capsys.readouterr().out
        assert 'alpha output requires WebM' in out
        assert 'out.mp4' in out and 'out.webm' in out

    def test_alpha_collision_writes_new_suffix(self, monkeypatch, tmp_path,
                                               capsys):
        (tmp_path / 'out.webm').write_bytes(b'')
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--background-alpha',
                          '0.0'])
        assert code == 1
        assert '_new' in capsys.readouterr().out

    def test_opaque_alpha_keeps_mp4(self, monkeypatch, tmp_path, capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--background-alpha',
                          '1'])
        assert code == 1
        out = capsys.readouterr().out
        assert 'alpha output requires WebM' not in out

    def test_alpha_clamping(self, monkeypatch, tmp_path, capsys):
        # 1.5 must clamp to opaque -> no WebM notice
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--background-alpha',
                          '1.5'])
        assert code == 1
        assert 'alpha output requires WebM' not in capsys.readouterr().out


class TestFontColorColorizedConflict:
    def test_both_flags_abort_with_message(self, monkeypatch, tmp_path,
                                           capsys):
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--colorized',
                          '--font-color', '#FF0000'])
        assert code == 1
        assert 'mutually exclusive' in capsys.readouterr().out

    def test_colorized_quickstart_contract(self, monkeypatch, tmp_path,
                                           capsys):
        """README promises `--colorized` works standalone. This holds
        the post-fix contract: colorized alone reaches the probe stage
        without tripping the mutual-exclusion check."""
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--colorized'])
        assert code == 1  # halted at probe (the mocked HALT)
        assert 'mutually exclusive' not in capsys.readouterr().out


class TestCliErrorHandling:
    def test_pipeline_exception_maps_to_exit_1(self, monkeypatch, tmp_path,
                                               capsys):
        """Exception contract between layers: run_conversion raises,
        the CLI prints ERROR and returns 1."""
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4'])
        assert code == 1
        out = capsys.readouterr().out
        assert 'ERROR: HALT_AFTER_PREFLIGHT' in out


class TestReuseResolution:
    def test_incomplete_reuse_dir_is_a_clean_error(self, monkeypatch, tmp_path, capsys):
        """validate_reuse() failures exit 1 with a message, not a traceback."""
        reuse_dir = make_artifacts(tmp_path / 'kept', with_txt=False)
        code = call_main(monkeypatch, tmp_path, [
            'in.mp4', 'out.mp4', '--reuse', 'ascii', '--reuse-dir', str(reuse_dir)])
        assert code == 1
        out = capsys.readouterr().out
        assert "Error: --reuse ascii: missing or empty" in out
        assert 'HALT_AFTER_PREFLIGHT' not in out      # never reached the pipeline

    def test_ignored_flags_come_from_argv_passed_to_main(self, monkeypatch, tmp_path,
                                                         capsys):
        """main(argv) must not consult sys.argv when argv is given."""
        reuse_dir = make_artifacts(tmp_path / 'kept')
        monkeypatch.setattr(sys, 'argv', ['asciify_video', '--tolerance=0.3'])
        call_main(monkeypatch, tmp_path, [
            'in.mp4', 'out.mp4', '--reuse', 'final', '--reuse-dir', str(reuse_dir),
            '--ramp', 'jp2a'])
        out = capsys.readouterr().out
        assert '!! --ramp was passed but is IGNORED (--reuse final)' in out
        assert '!! --tolerance' not in out

    def test_valid_reuse_dir_reaches_the_pipeline(self, monkeypatch, tmp_path, capsys):
        reuse_dir = make_artifacts(tmp_path / 'kept')
        code = call_main(monkeypatch, tmp_path, [
            'in.mp4', 'out.mp4', '--reuse', 'extracted', '--reuse-dir', str(reuse_dir)])
        out = capsys.readouterr().out
        assert code == 1 and 'ERROR: HALT_AFTER_PREFLIGHT' in out
        assert 'Level: extracted  (frames=3, ascii=3, rendered=3)' in out


class TestMainDispatch:
    def test_success_returns_zero_and_passes_config(self, monkeypatch, tmp_path):
        run = MagicMock()
        monkeypatch.setattr(cli, 'run_conversion', run)
        (tmp_path / 'in.mp4').write_bytes(b'')
        monkeypatch.chdir(tmp_path)
        assert cli.main(['in.mp4', 'out.mp4', '--ascii-width', '80']) == 0
        cfg = run.call_args.args[0]
        assert (cfg.input, cfg.output, cfg.ascii_width) == (
            Path('in.mp4'), Path('out.mp4'), 80)
        assert cfg.reuse_dir is None

    def test_keyboard_interrupt_returns_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, 'run_conversion',
                            MagicMock(side_effect=KeyboardInterrupt))
        (tmp_path / 'in.mp4').write_bytes(b'')
        monkeypatch.chdir(tmp_path)
        assert cli.main(['in.mp4', 'out.mp4']) == 1

    def test_argv_defaults_to_sys_argv(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv', ['asciify_video', '--state-file'])
        assert cli.main() == 0
        assert json.loads(capsys.readouterr().out)['version'] == 1

    def test_python_dash_m_entry_point(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv', ['asciify_video', '--report'])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module('asciify_video', run_name='__main__')
        assert exit_info.value.code == 0
        assert 'no state recorded yet' in capsys.readouterr().out


class TestGridWidthOptions:
    def build(self, *flags):
        args = cli.build_parser().parse_args(['in.mp4', 'out.mp4', *flags])
        return cli.build_config(args, Path('in.mp4'), Path('out.mp4'))

    def test_width_is_derived_unless_given(self):
        cfg = self.build()
        assert (cfg.ascii_width, cfg.scale_divisor) == (None, 8)

    def test_both_options_reach_the_config(self):
        cfg = self.build('--scale-divisor', '4', '--ascii-width', '120')
        assert (cfg.ascii_width, cfg.scale_divisor) == (120, 4)

    @pytest.mark.parametrize('flag', ['--scale-divisor', '--ascii-width'])
    @pytest.mark.parametrize('value', ['0', '-8'])
    def test_non_positive_values_rejected(self, capsys, flag, value):
        with pytest.raises(SystemExit) as exit_info:
            self.build(flag, value)
        assert exit_info.value.code == 1
        assert f'Error: {flag} must be a positive integer (got {value})' \
            in capsys.readouterr().out


class TestIterationWorkflow:
    def test_keep_temp_then_repeated_reuse_without_reuse_dir(self, fake_ffmpeg, tmp_path,
                                                             monkeypatch):
        """The README workflow: one --keep-temp run, then any number of --reuse runs."""
        (tmp_path / 'in.mp4').write_bytes(b'')
        monkeypatch.chdir(tmp_path)
        base = ['in.mp4', 'out.mp4', '--jobs', '1', '--silent-progress']
        assert cli.main(base + ['--keep-temp']) == 0
        for level in ('ascii', 'final', 'extracted'):
            assert cli.main(base + ['--reuse', level]) == 0
        fake_ffmpeg.probe.assert_called_once()
