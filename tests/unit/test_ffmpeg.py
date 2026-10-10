"""ffmpeg/ffprobe wrappers: every command is faked and asserted."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from PIL import Image

from asciify_video import (
    ffmpeg,
)
from tests.helpers import ffprobe_stdout


class TestProbeVideo:
    def test_parses_streams(self, monkeypatch, tmp_path):
        mock = MagicMock()
        mock.stdout = ffprobe_stdout(1280, 720, '24000/1001', audio=True)
        monkeypatch.setattr(ffmpeg, 'run_command',
                            MagicMock(return_value=mock))
        info = ffmpeg.probe_video(tmp_path / 'x.mp4')
        assert info == {'width': 1280, 'height': 720,
                        'fps': pytest.approx(24000 / 1001),
                        'has_audio': True}

    def test_no_audio(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=ffprobe_stdout(audio=False))
        monkeypatch.setattr(ffmpeg, 'run_command',
                            MagicMock(return_value=mock))
        assert ffmpeg.probe_video(tmp_path / 'x.mp4')['has_audio'] is False

    def test_no_video_stream_raises(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=json.dumps({'streams': [
            {'codec_type': 'audio'}]}))
        monkeypatch.setattr(ffmpeg, 'run_command',
                            MagicMock(return_value=mock))
        with pytest.raises(ValueError, match='No video stream'):
            ffmpeg.probe_video(tmp_path / 'x.mp4')

    def test_missing_fields_use_defaults(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=json.dumps({'streams': [
            {'codec_type': 'video'}]}))
        monkeypatch.setattr(ffmpeg, 'run_command',
                            MagicMock(return_value=mock))
        info = ffmpeg.probe_video(tmp_path / 'x.mp4')
        assert info == {'width': 1280, 'height': 720, 'fps': 30.0,
                        'has_audio': False}


class TestChromakeyCommand:
    def _capture(self, monkeypatch):
        holder = {}

        def fake_run(cmd, check=True):
            holder['cmd'] = cmd
            return MagicMock(returncode=0)
        monkeypatch.setattr(ffmpeg, 'run_command', fake_run)
        return holder

    def test_filter_graph(self, monkeypatch, tmp_path):
        holder = self._capture(monkeypatch)
        ffmpeg.chromakey_to_black(tmp_path / 'in.mp4',
                                   tmp_path / 'keyed.mp4',
                                   '0x00B140', 0.3, 0.1, 1920, 1080, 29.97)
        cmd = holder['cmd']
        assert cmd[0] == 'ffmpeg'
        filt = cmd[cmd.index('-filter_complex') + 1]
        assert 'chromakey=0x00B140:0.3:0.1[fg]' in filt
        assert 'color=c=black:s=1920x1080:r=29.97[bg]' in filt
        assert '[bg][fg]overlay=shortest=1,format=yuv420p[v]' in filt
        assert cmd[cmd.index('-c:v') + 1] == 'libx264'
        assert '-map' in cmd and '0:a?' in cmd  # optional audio passthrough

    def test_returns_output_path(self, monkeypatch, tmp_path):
        self._capture(monkeypatch)
        out = ffmpeg.chromakey_to_black(tmp_path / 'i', tmp_path / 'o',
                                         '0x00FF00', 0.25, 0.05, 640, 480, 30)
        assert out == tmp_path / 'o'


class TestExtractFrames:
    def test_scale_filter_and_sorted_glob(self, monkeypatch, tmp_path):
        frames_dir = tmp_path / 'frames'

        def fake_run(cmd, check=True):
            # Fake ffmpeg "producing" the frames it would have written
            vf = cmd[cmd.index('-vf') + 1]
            assert vf == 'scale=240:-2:flags=lanczos,fps=25'
            frames_dir.mkdir(exist_ok=True)
            for i in (2, 1, 3):
                (frames_dir / f'frame_{i:06d}.png').write_bytes(b'')
            return MagicMock(returncode=0)

        monkeypatch.setattr(ffmpeg, 'run_command', fake_run)
        frames = ffmpeg.extract_frames(tmp_path / 'k.mp4', frames_dir,
                                        target_fps=25, ascii_width=240)
        assert [f.name for f in frames] == [
            'frame_000001.png', 'frame_000002.png', 'frame_000003.png']


class TestAssembleVideo:
    def _run(self, monkeypatch, tmp_path, *, alpha, fps=24, has_audio=True,
             frame_size=(320, 160), target=(320, 160), source_exists=True,
             encoders_include_opus=True):
        img_dir = tmp_path / 'images'
        img_dir.mkdir()
        img_path = img_dir / 'rendered_000001.png'
        Image.new('RGBA', frame_size, 'black').save(str(img_path))
        rendered = [img_path]

        source = tmp_path / 'src.mp4'
        if source_exists:
            source.write_bytes(b'')

        monkeypatch.setattr(ffmpeg.subprocess, 'run',
                            lambda *a, **k: MagicMock(
                                returncode=0,
                                stdout=('... libopus ...' if
                                        encoders_include_opus else '')))
        spy = {}
        real = ffmpeg.run_command

        def capturing(cmd, check=True):
            spy['cmd'] = cmd
            return real(cmd, check=False)
        monkeypatch.setattr(ffmpeg, 'run_command', capturing)

        ffmpeg.assemble_video(rendered, tmp_path / 'out',
                               fps, source if source_exists else None,
                               has_audio, *target, check_frames=True,
                               bg_alpha=alpha, vpx_preset='fast')
        return spy['cmd']

    def test_opaque_mp4_pipeline(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, alpha=1.0)
        assert '-c:v' in cmd and cmd[cmd.index('-c:v') + 1] == 'libx264'
        assert 'yuva420p' not in cmd
        assert cmd[cmd.index('-c:a') + 1] == 'aac'
        assert '-framerate' in cmd and '-pattern_type' in cmd

    def test_alpha_webm_pipeline(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, alpha=0.5)
        # input-side pix_fmt to dodge the RGBA negotiation hang
        idx = cmd.index('-pix_fmt')
        assert cmd[idx + 1] == 'rgba' and idx < cmd.index('-i')
        assert cmd[cmd.index('-c:v') + 1] == 'libvpx-vp9'
        assert cmd[cmd.index('-pix_fmt', idx + 1) + 1] == 'yuva420p'
        assert cmd[cmd.index('-cpu-used') + 1] == '2'  # 'fast' preset
        assert cmd[cmd.index('-c:a') + 1] == 'libopus'

    def test_alpha_audio_vorbis_fallback(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, alpha=0.0,
                        encoders_include_opus=False)
        assert cmd[cmd.index('-c:a') + 1] == 'libvorbis'

    def test_frame_size_mismatch_adds_scale(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, alpha=1.0,
                        frame_size=(640, 320), target=(320, 160))
        vf = cmd[cmd.index('-vf') + 1]
        assert 'scale=320:160' in vf and 'lanczos' in vf

    def test_frame_at_target_no_scale(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, alpha=1.0)
        assert '-vf' not in cmd


class TestVpxPreset:
    def test_vpx_preset_mappings(self):
        for preset, expected in [('ultrafast', 8), ('veryfast', 6),
                                 ('faster', 4), ('fast', 2),
                                 ('medium', 1), ('slow', 0),
                                 ('bogus', 4)]:
            assert ffmpeg.vpx_speed_to_int(preset) == expected


class TestRunCommand:
    def test_failure_raises_with_command_and_prints_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(ffmpeg.subprocess, 'run', lambda *a, **k: MagicMock(
            returncode=1, stderr='no such file'))
        with pytest.raises(RuntimeError, match='Command failed: ffmpeg -i x'):
            ffmpeg.run_command(['ffmpeg', '-i', 'x'])
        assert 'STDERR: no such file' in capsys.readouterr().out

    def test_failure_tolerated_when_check_disabled(self, monkeypatch):
        failed = MagicMock(returncode=1, stderr='')
        monkeypatch.setattr(ffmpeg.subprocess, 'run', lambda *a, **k: failed)
        assert ffmpeg.run_command(['ffprobe'], check=False) is failed


class TestAssembleHelpers:
    @pytest.mark.parametrize('error', [
        FileNotFoundError('ffmpeg'),
        subprocess.TimeoutExpired('ffmpeg', 5),
    ])
    def test_encoder_probe_failure_means_unavailable(self, monkeypatch, error):
        monkeypatch.setattr(ffmpeg.subprocess, 'run', MagicMock(side_effect=error))
        assert ffmpeg._has_encoder('libopus') is False

    def test_missing_frame_forces_scale(self, tmp_path):
        assert ffmpeg._scale_args(tmp_path / 'ghost.png', 640, 320) == [
            '-vf', 'scale=640:320:flags=lanczos']

    def test_unreadable_frame_forces_scale_with_warning(self, tmp_path, capsys):
        broken = tmp_path / 'rendered_000001.png'
        broken.write_bytes(b'not a png')
        assert ffmpeg._scale_args(broken, 640, 320) == [
            '-vf', 'scale=640:320:flags=lanczos']
        assert 'Could not probe frame size' in capsys.readouterr().out

    def test_vp9_args_use_only_real_encoder_options(self):
        """'-realtime' is not an ffmpeg option; realtime mode is '-deadline realtime'."""
        args = ffmpeg._vp9_alpha_args('fast')
        assert '-realtime' not in args
        assert args[args.index('-deadline') + 1] == 'realtime'
        assert args[args.index('-cpu-used') + 1] == '2'
        assert args[args.index('-row-mt') + 1] == '1'


class TestExtractFramesWithoutFps:
    def test_no_fps_filter_when_rate_not_given(self, monkeypatch, tmp_path):
        commands = []
        monkeypatch.setattr(ffmpeg, 'run_command', lambda cmd, check=True: commands.append(cmd))
        ffmpeg.extract_frames(tmp_path / 'in.mp4', tmp_path / 'frames', ascii_width=120)
        assert commands[0][commands[0].index('-vf') + 1] == 'scale=120:-2:flags=lanczos'
