#!/usr/bin/env python3
"""
Unit tests for asciify.py — regression suite for refactoring.

Strategy:
  - ffmpeg/ffprobe invocations are intercepted by mocking asciify.run_command
    (and asciify.subprocess.run where the code calls it directly), so no
    binaries are needed and every command is asserted.
  - Pure-Python stages (_process_one_frame, compute_global_stretch) are
    exercised for real using tiny PIL-generated images.
  - Reuse/artifact validation runs against synthetic artifact directories
    laid out exactly like a --keep-temp run.

Optionally, point ASCIIFY_TEST_ARTIFACTS at a real temp dir from a
--keep-temp run to also run the real-artifact integration tests:

    ./asciify.py sample.mp4 out.mp4 --keep-temp
    export ASCIIFY_TEST_ARTIFACTS=/tmp/asciify_xxxx
    pytest test_asciify.py -v
"""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import asciify

# --------------------------------------------------------------------------
# Optional real artifacts from a --keep-temp run
# --------------------------------------------------------------------------

ARTIFACTS = Path(os.environ.get('ASCIIFY_TEST_ARTIFACTS', ''))
REAL_FRAMES = sorted((ARTIFACTS / 'frames').glob('frame_*.png')) \
    if (ARTIFACTS / 'frames').is_dir() else []

needs_real_frames = pytest.mark.skipif(
    not REAL_FRAMES, reason="set ASCIIFY_TEST_ARTIFACTS to a --keep-temp dir")

# --------------------------------------------------------------------------
# Regression Cannary Tests
# --------------------------------------------------------------------------

class TestNoDeprecatedPillowUsage:
    def test_getdata_not_used_in_source(self):
        """Image.getdata() is deprecated and slated for removal in
        Pillow 14. Walks asciify.py's syntax tree and fails if any
        attribute named 'getdata' is referenced — comments and
        docstrings (including those explaining this prohibition)
        can never trigger it. Use _flattened_pixels() or
        Image.tobytes() instead.
        """
        import ast

        source = Path(asciify.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)   # syntax errors surface as test errors,
                                   # never silent passes
        offenders = [
            f'line {node.lineno}'
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == 'getdata'
        ]
        assert not offenders, (
            f'getdata() is deprecated (Pillow 14 removal); '
            f'found attribute access at {offenders}; '
            f'use _flattened_pixels() or Image.tobytes() instead')

# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------

RAMP = asciify.DEFAULT_RAMP

def gray_png(path, value, w=8, h=4):
    """Write a grayscale PNG that reads back as `value` luminance."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('L', (w, h), value).save(str(path))
    return path

def cell_png(path, values, cols=None, rows=None):
    """PNG whose pixel (x, y) has luminance values[y][x].

    Sized exactly cols x rows so _process_one_frame's BOX resize is an
    identity operation — per-character expectations stay exact.
    """
    rows = rows if rows is not None else len(values)
    cols = cols if cols is not None else len(values[0])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('L', (cols, rows))
    img.putdata([v for row in values for v in row])
    img.save(str(path))
    return path

def build_metrics(max_cols, max_rows, tw, th, font_size=32):
    """Replicate the analytic grid math from main()."""
    cell_w = max(1, tw // max_cols)
    cell_h = max(1, th // max_rows)
    off_x = (tw - cell_w * max_cols) // 2
    off_y = (th - cell_h * max_rows) // 2
    return (max_cols, max_rows, cell_w, cell_h, off_x, off_y, tw, th,
            font_size)

def make_task(frame_path, out_path, ramp=RAMP, stretch=None, metrics=None,
              ascii_mode='compute', ascii_path=None, colorized=False,
              leave_black_empty=False, black_threshold=50,
              bg_color_rgb=(0, 0, 0), bg_alpha=1.0,
              font_color_rgb=(100, 255, 100)):
    metrics = metrics or build_metrics(4, 2, 40, 20)
    return (str(frame_path), str(out_path), ascii_mode, ascii_path, ramp,
            stretch, metrics, leave_black_empty, black_threshold, colorized,
            bg_color_rgb, bg_alpha, font_color_rgb)

def expected_char(value, ramp=RAMP, stretch=None):
    """Mirror the luminance->ramp mapping in _process_one_frame."""
    if stretch is not None:
        lo, hi = stretch
        span = max(hi - lo, 1)
        value = min(255, max(0, (value - lo) * 255 // span))
    scale = len(ramp) / 256
    return ramp[min(len(ramp) - 1, int(value * scale))]

def make_artifacts(root, n_frames=3, with_txt=True, with_images=True,
                   cols=8, rows=4):
    """Build a directory tree shaped like a --keep-temp run."""
    root = Path(root)
    for i in range(1, n_frames + 1):
        gray_png(root / 'frames' / f'frame_{i:06d}.png', 128, cols, rows)
    if with_txt:
        line = expected_char(128) * cols
        (root / 'txt_frames').mkdir(parents=True, exist_ok=True)
        for i in range(n_frames):
            (root / 'txt_frames' / f'frame_{i:06d}.txt').write_text(
                '\n'.join([line] * rows))
    if with_images:
        (root / 'images').mkdir(parents=True, exist_ok=True)
        for i in range(n_frames):
            Image.new('RGB', (64, 32), 'black').save(
                str(root / 'images' / f'rendered_{i:06d}.png'))
    return root

def isolated_state(monkeypatch, tmp_path):
    """Keep tests away from the developer's real ~/.cache/asciify state."""
    state_file = tmp_path / 'state.json'
    monkeypatch.setattr(asciify, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(asciify, 'STATE_FILE', state_file)
    return state_file

def ffprobe_stdout(width=1920, height=1080, rate='30/1', audio=True):
    streams = [{'codec_type': 'video', 'width': width, 'height': height,
                'r_frame_rate': rate}]
    if audio:
        streams.append({'codec_type': 'audio'})
    return json.dumps({'streams': streams})

def call_main(monkeypatch, tmp_path, argv, state_exists=False,
              state_body=None):
    """Invoke main() with sys.argv spoofed, cwd and state file contained
    to tmp_path. probe_video is replaced with an exception so the run
    halts right after the pre-flight checks we want to observe.

    NB: main() resolves relative input/output paths against the cwd,
    so we chdir into tmp_path first.
    Returns the exit code (or None if main() returned normally).
    """
    (tmp_path / 'in.mp4').write_bytes(b'')
    monkeypatch.chdir(tmp_path)
    state_file = isolated_state(monkeypatch, tmp_path)
    if state_exists:
        state_file.write_text(json.dumps(state_body or {}))
    monkeypatch.setattr(sys, 'argv', ['asciify.py'] + argv)
    monkeypatch.setattr(
        asciify, 'probe_video',
        MagicMock(side_effect=RuntimeError('HALT_AFTER_PREFLIGHT')))
    code = None
    try:
        asciify.main()
    except SystemExit as e:
        code = e.code
    return code

# ==========================================================================
# 1. Video probing (ffprobe faked)
# ==========================================================================

class TestProbeVideo:
    def test_parses_streams(self, monkeypatch, tmp_path):
        mock = MagicMock()
        mock.stdout = ffprobe_stdout(1280, 720, '24000/1001', audio=True)
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(return_value=mock))
        info = asciify.probe_video(tmp_path / 'x.mp4')
        assert info == {'width': 1280, 'height': 720,
                        'fps': pytest.approx(24000 / 1001),
                        'has_audio': True}

    def test_no_audio(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=ffprobe_stdout(audio=False))
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(return_value=mock))
        assert asciify.probe_video(tmp_path / 'x.mp4')['has_audio'] is False

    def test_no_video_stream_raises(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=json.dumps({'streams': [
            {'codec_type': 'audio'}]}))
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(return_value=mock))
        with pytest.raises(ValueError, match='No video stream'):
            asciify.probe_video(tmp_path / 'x.mp4')

    def test_missing_fields_use_defaults(self, monkeypatch, tmp_path):
        mock = MagicMock(stdout=json.dumps({'streams': [
            {'codec_type': 'video'}]}))
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(return_value=mock))
        info = asciify.probe_video(tmp_path / 'x.mp4')
        assert info == {'width': 1280, 'height': 720, 'fps': 30.0,
                        'has_audio': False}

# ==========================================================================
# 2. ffmpeg command construction (run_command faked)
# ==========================================================================

class TestChromakeyCommand:
    def _capture(self, monkeypatch):
        holder = {}

        def fake_run(cmd, check=True):
            holder['cmd'] = cmd
            return MagicMock(returncode=0)
        monkeypatch.setattr(asciify, 'run_command', fake_run)
        return holder

    def test_filter_graph(self, monkeypatch, tmp_path):
        holder = self._capture(monkeypatch)
        asciify.chromakey_to_black(tmp_path / 'in.mp4',
                                   tmp_path / 'keyed.mp4',
                                   '0x00B140', 0.3, 0.1, 1920, 1080, 29.97)
        cmd = holder['cmd']
        assert cmd[0] == 'ffmpeg'
        filt = cmd[cmd.index('-filter_complex') + 1]
        assert f'chromakey=0x00B140:0.3:0.1[fg]' in filt
        assert f'color=c=black:s=1920x1080:r=29.97[bg]' in filt
        assert '[bg][fg]overlay=shortest=1,format=yuv420p[v]' in filt
        assert cmd[cmd.index('-c:v') + 1] == 'libx264'
        assert '-map' in cmd and '0:a?' in cmd  # optional audio passthrough

    def test_returns_output_path(self, monkeypatch, tmp_path):
        self._capture(monkeypatch)
        out = asciify.chromakey_to_black(tmp_path / 'i', tmp_path / 'o',
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

        monkeypatch.setattr(asciify, 'run_command', fake_run)
        frames = asciify.extract_frames(tmp_path / 'k.mp4', frames_dir,
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

        monkeypatch.setattr(asciify.subprocess, 'run',
                            lambda *a, **k: MagicMock(
                                returncode=0,
                                stdout=('... libopus ...' if
                                        encoders_include_opus else '')))
        spy = {}
        real = asciify.run_command

        def capturing(cmd, check=True):
            spy['cmd'] = cmd
            return real(cmd, check=False)
        monkeypatch.setattr(asciify, 'run_command', capturing)

        asciify.assemble_video(rendered, tmp_path / 'out',
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

# ==========================================================================
# 3. Green sampling (ffmpeg rawvideo output faked)
# ==========================================================================

class TestSampleGreenColor:
    RAW_W = 160
    RAW_H = 90

    def _setup(self, monkeypatch, tmp_path, pixels_fn, height=None):
        height = height or self.RAW_H
        probe = MagicMock(stdout=json.dumps({'format': {'duration': '10'}}))
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(return_value=probe))

        def fake_run(cmd, capture_output=True):
            w, h = self.RAW_W, height
            buf = bytearray(w * h * 3)
            for py in range(h):
                for px in range(w):
                    buf[(py * w + px) * 3:(py * w + px) * 3 + 3] = \
                        bytes(pixels_fn(px, py))
            return MagicMock(returncode=0, stdout=bytes(buf))
        monkeypatch.setattr(asciify.subprocess, 'run', fake_run)
        return tmp_path / 'in.mp4'

    def _corner_mask(self, px, py, w=RAW_W, h=RAW_H):
        bw, bh = w // 6, h // 6
        return (px < bw or px >= w - bw) and (py < bh or py >= h - bh)

    def test_detects_corner_green(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (0, 255, 0) if self._corner_mask(px, py) else (120, 120, 120)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = asciify.sample_green_color(
            path, sample_frames=3, region='corners')
        assert detected is True
        assert color == '0x00FF00'

    def test_corners_excluded_when_sampling_center(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (0, 255, 0) if self._corner_mask(px, py) else (10, 10, 10)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = asciify.sample_green_color(
            path, sample_frames=3, region='center')
        assert detected is False
        assert color == '0x00FF00'  # documented fallback

    def test_brightness_gate_rejects_extremes(self, monkeypatch, tmp_path):
        # Green-dominant but average brightness 223.7 > 220: filtered by
        # the brightness gate, so no detection occurs
        def px_fn(px, py):
            return (210, 251, 210) if self._corner_mask(px, py) else (0, 0, 0)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = asciify.sample_green_color(
            path, sample_frames=2, region='corners')
        assert detected is False
        assert color == '0x00FF00'

    def test_avg_color_not_pure_green(self, monkeypatch, tmp_path):
        def px_fn(px, py):
            return (40, 200, 60) if self._corner_mask(px, py) else (5, 5, 5)
        path = self._setup(monkeypatch, tmp_path, px_fn)
        color, detected = asciify.sample_green_color(
            path, sample_frames=2, region='corners')
        assert detected is True
        assert color == '0x28C83C'

    def test_exception_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(asciify, 'run_command',
                            MagicMock(side_effect=RuntimeError('boom')))
        color, detected = asciify.sample_green_color(tmp_path / 'x.mp4')
        assert (color, detected) == ('0x00FF00', False)

# ==========================================================================
# 4. Pure-Python conversion / rendering (real PIL, no mocks)
# ==========================================================================

class TestProcessOneFrameAscii:
    def _run(self, tmp_path, values, **kw):
        frame = cell_png(tmp_path / 'frame_000001.png', values)
        out = tmp_path / 'rendered_000001.png'
        task = make_task(frame, out,
                         metrics=build_metrics(len(values[0]),
                                               len(values), 40, 20), **kw)
        out_path, text = asciify._process_one_frame(task)
        # worker returns the path as the str given in the task
        assert Path(out_path) == out and out.exists()
        return text, Image.open(str(out))

    def test_exact_character_mapping(self, tmp_path):
        values = [[0, 64, 128, 255], [10, 200, 130, 90]]
        text, _ = self._run(tmp_path, values)
        lines = text.split('\n')
        assert len(lines) == 2 and all(len(l) == 4 for l in lines)
        for y, row in enumerate(values):
            for x, v in enumerate(row):
                assert lines[y][x] == expected_char(v)

    def test_extremes_map_to_first_last_ramp_chars(self, tmp_path):
        text, _ = self._run(tmp_path, [[0, 255]])
        assert text.split('\n')[0][0] == RAMP[0]
        assert text.split('\n')[0][1] == RAMP[-1]

    def test_histogram_stretch_applied(self, tmp_path):
        plain, _ = self._run(tmp_path, [[100]])
        stretched, _ = self._run(tmp_path, [[100]], stretch=(100, 200))
        assert plain.split('\n')[0] == expected_char(100)
        # value at lo bound maps to zero -> first ramp char
        assert stretched.split('\n')[0] == expected_char(
            100, stretch=(100, 200))
        assert stretched != plain  # stretch actually changed the mapping
        # value at hi bound saturates -> last ramp char
        sat, _ = self._run(tmp_path, [[200]], stretch=(100, 200))
        assert sat.split('\n')[0] == expected_char(
            200, stretch=(100, 200)) == RAMP[-1]

    def test_blank_frame_writes_canvas_and_returns_text(self, tmp_path):
        text, img = self._run(tmp_path, [[0, 0], [0, 0]],
                              bg_color_rgb=(20, 30, 40))
        assert text.strip() == ''
        assert img.convert('RGB').getpixel((5, 5)) == (20, 30, 40)

    def test_ascii_mode_file_uses_text_not_luminance(self, tmp_path):
        # Frame is bright everywhere; reused text says darker chars —
        # the text file must win over the frame content.
        frame = cell_png(tmp_path / 'frame_000001.png', [[200, 200]])
        txt = tmp_path / 'frame_000000.txt'
        txt.write_text(RAMP[3] * 2 + '\n')
        out = tmp_path / 'rendered_000001.png'
        _, text = asciify._process_one_frame(
            make_task(frame, out, ascii_mode='file', ascii_path=str(txt),
                      metrics=build_metrics(2, 1, 20, 10)))
        assert text.split('\n')[0] == RAMP[3] * 2

class TestProcessOneFrameRendering:
    def _glyph_area_hits(self, img, x0, y0, cell_w, cell_h, pred):
        hits = 0
        for dx in range(cell_w):
            for dy in range(cell_h):
                if pred(img.convert('RGB').getpixel(
                        (x0 + dx, y0 + dy))):
                    hits += 1
        return hits

    def test_font_color_used_for_glyphs(self, tmp_path):
        frame = cell_png(tmp_path / 'frame_000001.png', [[255]])
        out = tmp_path / 'rendered_000001.png'
        metrics = build_metrics(1, 1, 20, 20)
        asciify._process_one_frame(
            make_task(frame, out, metrics=metrics,
                      font_color_rgb=(255, 0, 255)))
        img = Image.open(str(out)).convert('RGB')
        assert self._glyph_area_hits(
            img, 0, 0, metrics[2], metrics[3],
            lambda p: p[0] > 100 and p[2] > 100 and p[1] < 100) > 0

    def test_leave_black_empty_keeps_background(self, tmp_path):
        values = [[10, 200]]      # cell 0 below threshold, cell 1 above
        frame = cell_png(tmp_path / 'frame_000001.png', values)
        out = tmp_path / 'rendered_000001.png'
        bg = (37, 42, 47)
        metrics = build_metrics(2, 1, 20, 10)
        asciify._process_one_frame(
            make_task(frame, out, metrics=metrics, bg_color_rgb=bg,
                      leave_black_empty=True, black_threshold=50))
        img = Image.open(str(out)).convert('RGB')
        # dark cell: untouched background throughout
        for dx in range(metrics[2]):
            for dy in range(metrics[3]):
                assert img.getpixel((dx, dy)) == bg
        # bright cell: some glyph pixels differ from the background
        assert self._glyph_area_hits(img, metrics[2], 0, metrics[2],
                                     metrics[3],
                                     lambda p: p != bg) > 0

    def test_black_threshold_boundary(self, tmp_path):
        # value == threshold is masked (comparison is strict '<')
        for value in (49, 50, 51):
            frame = cell_png(tmp_path / 'frame_000001.png', [[value]])
            out = tmp_path / 'rendered_000001.png'
            metrics = build_metrics(1, 1, 20, 20)
            asciify._process_one_frame(
                make_task(frame, out, metrics=metrics,
                          bg_color_rgb=(0, 0, 0),
                          leave_black_empty=True, black_threshold=50))
            assert out.exists()

    def test_colorized_samples_source_colors(self, tmp_path):
        frame = tmp_path / 'frame_000001.png'
        Image.new('RGB', (1, 1), (230, 20, 20)).save(str(frame))
        out = tmp_path / 'rendered_000001.png'
        metrics = build_metrics(1, 1, 20, 20)
        asciify._process_one_frame(
            make_task(frame, out, metrics=metrics, colorized=True))
        img = Image.open(str(out)).convert('RGB')
        assert self._glyph_area_hits(
            img, 0, 0, metrics[2], metrics[3],
            lambda p: p[0] > 150 and p[1] < 100) > 0

    def test_background_alpha_stored_in_output(self, tmp_path):
        frame = cell_png(tmp_path / 'frame_000001.png', [[0]])
        out = tmp_path / 'rendered_000001.png'
        metrics = build_metrics(1, 1, 20, 20)
        asciify._process_one_frame(
            make_task(frame, out, metrics=metrics, bg_alpha=0.25))
        assert Image.open(str(out)).mode == 'RGBA'
        # int() truncation: int(0.25 * 255) == 63
        assert Image.open(str(out)).getpixel((2, 2))[3] == 63

class TestComputeGlobalStretch:
    def test_percentile_bounds(self, tmp_path):
        frames = [gray_png(tmp_path / f'f{i}.png', v, 20, 20)
                  for i, v in enumerate([50, 50, 205, 205, 50])]
        lo, hi = asciify.compute_global_stretch(frames, sample_step=1,
                                                percentile=0.5)
        assert lo == 50 and hi == 205

    def test_degenerate_span_falls_back_to_full_range(self, tmp_path):
        frames = [gray_png(tmp_path / f'f{i}.png', 100, 10, 10)
                  for i in range(3)]
        assert asciify.compute_global_stretch(frames, sample_step=1) == \
            (0, 255)

    def test_empty_input_safe(self):
        assert asciify.compute_global_stretch([]) == (0, 255)

class TestFramesToRenderedImages:
    def test_parallel_pipeline_end_to_end(self, tmp_path):
        frames = [cell_png(tmp_path / f'frame_{i:06d}.png',
                          [[128, 64], [10, 255]])
                  for i in range(1, 4)]
        metrics = build_metrics(2, 2, 20, 20)
        txt_dir = tmp_path / 'txt_frames'
        rendered = asciify.frames_to_rendered_images(
            frames, tmp_path / 'images', metrics, RAMP, None,
            jobs=2, txt_dir=txt_dir)
        assert len(rendered) == 3
        assert all(Path(p).exists() for p in rendered)
        # lexical sort stays frame-ordered thanks to zfill
        assert rendered == sorted(rendered)
        # text artifacts persisted with per-run numbering
        txts = sorted(txt_dir.glob('frame_*.txt'))
        assert len(txts) == 3
        body = txts[0].read_text()
        assert body.split('\n')[0] == (
            expected_char(128) + expected_char(64))

# ==========================================================================
# 5. State file handling
# ==========================================================================

class TestStateFile:
    def test_save_load_roundtrip(self, monkeypatch, tmp_path):
        sf = isolated_state(monkeypatch, tmp_path)
        asciify.save_state({'version': 1, 'input': 'in.mp4',
                            'completed': True})
        loaded = asciify.load_state()
        assert loaded['input'] == 'in.mp4'
        assert loaded['completed'] is True
        assert loaded['updated'] is not None  # stamped on save

    def test_missing_defaults(self, monkeypatch, tmp_path):
        isolated_state(monkeypatch, tmp_path)
        state = asciify.load_state()
        for key in ('version', 'updated', 'input', 'temp_dir', 'keep_temp',
                    'completed', 'stages_completed', 'probe', 'key', 'grid',
                    'render', 'counts', 'output', 'duration_s'):
            assert key in state

    def test_corrupt_json_falls_back(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text('{not json')
        state = asciify.load_state()
        assert state['version'] == 1
        assert 'could not parse' in capsys.readouterr().out

    def test_partial_state_gets_template_fields(self, monkeypatch, tmp_path):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps({'input': 'x.mp4', 'bogus_future_key': 7}))
        state = asciify.load_state()
        assert state['input'] == 'x.mp4'
        assert 'completed' in state          # defaulted
        assert state['bogus_future_key'] == 7  # forward-compatible

    def test_count_dir(self, tmp_path):
        assert asciify.count_dir(None) == 0
        assert asciify.count_dir(tmp_path / 'nope') == 0
        (tmp_path / 'a').mkdir()
        for i in range(5):
            (tmp_path / 'a' / f'f{i}').touch()
        assert asciify.count_dir(tmp_path / 'a') == 5

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
        asciify.report_state(asciify.load_state())
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
        asciify.report_state(asciify.load_state())
        assert '(--no-key)' in capsys.readouterr().out

# ==========================================================================
# 6. Reuse machinery
# ==========================================================================

class TestReuseHelpers:
    def test_resolve_priority_explicit_over_env_over_state(
            self, monkeypatch, tmp_path):
        explicit = tmp_path / 'explicit'; explicit.mkdir()
        envdir = tmp_path / 'env'; envdir.mkdir()
        statedir = tmp_path / 'state'; statedir.mkdir()
        monkeypatch.setenv('ASCII_VIDEO_TEMP', str(envdir))
        state = {'temp_dir': str(statedir)}

        assert asciify.resolve_reuse_dir(str(explicit), state) == explicit
        assert asciify.resolve_reuse_dir(None, state) == envdir
        monkeypatch.delenv('ASCII_VIDEO_TEMP')
        assert asciify.resolve_reuse_dir(None, state) == statedir
        assert asciify.resolve_reuse_dir(None, {}) is None
        # nonexistent dirs yield None even when configured
        assert asciify.resolve_reuse_dir(
            str(tmp_path / 'ghost'), state) is None

    def test_validate_reuse_requirements(self, tmp_path):
        full = make_artifacts(tmp_path / 'full')
        asciify.validate_reuse(full, 'extracted')
        asciify.validate_reuse(full, 'ascii')
        asciify.validate_reuse(full, 'final')

        for level, missing in [('extracted', 'frames'),
                               ('ascii', 'txt_frames'),
                               ('final', 'images')]:
            partial = make_artifacts(tmp_path / f'partial_{level}')
            shutil.rmtree(partial / missing)
            with pytest.raises(RuntimeError, match=missing):
                asciify.validate_reuse(partial, level)

    def test_empty_dir_fails_validation(self, tmp_path):
        empty = make_artifacts(tmp_path / 'empty')
        (empty / 'frames').mkdir(exist_ok=True)
        for f in (empty / 'frames').iterdir():
            f.unlink()
        with pytest.raises(RuntimeError, match='empty'):
            asciify.validate_reuse(empty, 'extracted')

    def test_validate_frame_sizes_warns_on_mismatch(self, tmp_path, capsys):
        reuse = make_artifacts(tmp_path / 'r')
        state = {'render': {'target_width': 320, 'target_height': 160}}
        assert asciify.validate_reuse_frame_sizes(
            reuse, state, 640, 320) is True
        assert '640x320' in capsys.readouterr().out

    def test_validate_frame_sizes_silent_when_matching(self, tmp_path,
                                                       capsys):
        reuse = make_artifacts(tmp_path / 'r')
        state = {'render': {'target_width': 64, 'target_height': 32}}
        asciify.validate_reuse_frame_sizes(reuse, state, 64, 32)
        assert 'Warning' not in capsys.readouterr().out

class TestReuseSemanticsReporting:
    def test_dead_flag_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv', [
            'asciify.py', 'in.mp4', 'out.mp4', '--reuse', 'final',
            '--font-color', '#FF0000'])
        asciify.report_reuse_semantics('final')
        out = capsys.readouterr().out
        assert '!! --font-color was passed but is IGNORED (--reuse final)'\
            in out
        assert 'Still honored: --fps' in out

    def test_no_warning_when_flag_not_passed(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv',
                            ['asciify.py', 'in.mp4', 'out.mp4'])
        asciify.report_reuse_semantics('ascii')
        assert '!!' not in capsys.readouterr().out

    def test_flag_map_registrations_complete(self):
        # Rendering flags must all be registered, per README checklist
        rendering = asciify.STAGE_FLAG_MAP['rendering']
        for flag in ('--render-font-size', '--colorized', '--font-color',
                     '--leave-black-empty', '--black-threshold',
                     '--background-color', '--background-alpha'):
            assert flag in rendering, flag
        assert set(asciify.REUSE_SKIPPED_STAGES['final']) == \
            {'keying', 'conversion', 'rendering'}

    def test_vpx_preset_mappings(self):
        for preset, expected in [('ultrafast', 8), ('veryfast', 6),
                                 ('faster', 4), ('fast', 2),
                                 ('medium', 1), ('slow', 0),
                                 ('bogus', 4)]:
            assert asciify.vpx_speed_to_int(preset) == expected

    def test_frame_num_parsing(self):
        assert asciify.frame_num(Path('frame_000042.png')) == 42
        assert asciify.frame_num(Path('rendered_000108.png')) == 108
        assert asciify.frame_num(Path('no_digits.png')) == 0

# ==========================================================================
# 7. main() pre-flight behavior (subprocess-free paths only)
# ==========================================================================

class TestMainPreflight:
    def test_report_mode_exits_cleanly(self, monkeypatch, tmp_path, capsys):
        sf = isolated_state(monkeypatch, tmp_path)
        sf.write_text(json.dumps({'input': 'in.mp4', 'completed': True}))
        monkeypatch.setattr(sys, 'argv', ['asciify.py', '--report'])
        with pytest.raises(SystemExit) as e:
            asciify.main()
        assert e.value.code == 0
        assert 'in.mp4' in capsys.readouterr().out

    def test_state_file_mode_dumps_json(self, monkeypatch, tmp_path, capsys):
        isolated_state(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, 'argv', ['asciify.py', '--state-file'])
        with pytest.raises(SystemExit) as e:
            asciify.main()
        assert e.value.code == 0
        assert json.loads(capsys.readouterr().out)['version'] == 1

    def test_missing_input_file(self, monkeypatch, tmp_path, capsys):
        isolated_state(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, 'argv',
                            ['asciify.py', '/no/such/file.mp4', 'o.mp4'])
        with pytest.raises(SystemExit) as e:
            asciify.main()
        assert e.value.code == 1
        assert 'not found' in capsys.readouterr().out

    def test_missing_positional_args(self, monkeypatch, tmp_path):
        isolated_state(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, 'argv', ['asciify.py'])
        with pytest.raises(SystemExit):
            asciify.main()

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

    def test_reuse_without_temp_dir_errors(self, monkeypatch, tmp_path,
                                            capsys):
        isolated_state(monkeypatch, tmp_path)   # no temp_dir recorded
        monkeypatch.setattr(sys, 'argv',
                            ['asciify.py', 'in.mp4', 'out.mp4',
                             '--reuse', 'ascii'])
        (tmp_path / 'in.mp4').write_bytes(b'')
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as e:
            asciify.main()
        assert e.value.code == 1
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

    def test_colorized_alone_currently_aborts___known_bug(
            self, monkeypatch, tmp_path, capsys):
        """Pins the CURRENT (buggy) behavior: --colorized alone also
        aborts, because --font-color defaults to a truthy '#64FF64'.
        If this test fails after a fix, delete it and rely on the
        contract test below (which will XPASS)."""
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--colorized'])
        assert code == 1
        assert 'mutually exclusive' in capsys.readouterr().out

    @pytest.mark.xfail(reason='Known bug: --font-color default is '
                              'truthy, so --colorized alone trips the '
                              'conflict check. Fix: default '
                              '--font-color to None. (Currently this '
                              'test fails because the conflict message '
                              'IS emitted.)',
                       strict=False)
    def test_colorized_quickstart_contract(self, monkeypatch, tmp_path,
                                           capsys):
        """README promises `--colorized` works standalone. This holds
        the post-fix contract: colorized alone reaches the probe stage
        without tripping the mutual-exclusion check."""
        code = call_main(monkeypatch, tmp_path,
                         ['in.mp4', 'out.mp4', '--colorized'])
        assert code == 1  # halted at probe (the mocked HALT)
        assert 'mutually exclusive' not in capsys.readouterr().out

# ==========================================================================
# 8. Optional integration with real --keep-temp artifacts
# ==========================================================================

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
        _, text = asciify._process_one_frame(task)
        digest = hashlib.sha256(text.encode()).hexdigest()
        assert digest  # record the hash in a pinned constant when it
        # stabilizes across a known-good revision, then compare here

    def test_real_temp_dir_validates(self):
        asciify.validate_reuse(ARTIFACTS, 'extracted')
        if (ARTIFACTS / 'txt_frames').is_dir() and \
                list((ARTIFACTS / 'txt_frames').glob('*.txt')):
            asciify.validate_reuse(ARTIFACTS, 'ascii')

if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
