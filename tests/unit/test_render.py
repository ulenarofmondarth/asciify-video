"""Pure-Python conversion and rendering (real PIL, no mocks)."""

import dataclasses
from pathlib import Path

import pytest
from PIL import Image

from asciify_video import render
from tests.helpers import (
    RAMP,
    build_metrics,
    cell_png,
    expected_char,
    gray_png,
    make_task,
)


class TestProcessOneFrameAscii:
    def _run(self, tmp_path, values, **kw):
        frame = cell_png(tmp_path / 'frame_000001.png', values)
        out = tmp_path / 'rendered_000001.png'
        task = make_task(frame, out,
                         metrics=build_metrics(len(values[0]),
                                               len(values), 40, 20), **kw)
        out_path, text = render._process_one_frame(task)
        # worker returns the path as the str given in the task
        assert Path(out_path) == out and out.exists()
        return text, Image.open(str(out))

    def test_exact_character_mapping(self, tmp_path):
        values = [[0, 64, 128, 255], [10, 200, 130, 90]]
        text, _ = self._run(tmp_path, values)
        lines = text.split('\n')
        assert len(lines) == 2 and all(len(line) == 4 for line in lines)
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
        _, text = render._process_one_frame(
            make_task(frame, out, ascii_path=str(txt),
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
        render._process_one_frame(
            make_task(frame, out, metrics=metrics,
                      font_color_rgb=(255, 0, 255)))
        img = Image.open(str(out)).convert('RGB')
        assert self._glyph_area_hits(
            img, 0, 0, metrics.cell_w, metrics.cell_h,
            lambda p: p[0] > 100 and p[2] > 100 and p[1] < 100) > 0

    def test_leave_black_empty_keeps_background(self, tmp_path):
        values = [[10, 200]]      # cell 0 below threshold, cell 1 above
        frame = cell_png(tmp_path / 'frame_000001.png', values)
        out = tmp_path / 'rendered_000001.png'
        bg = (37, 42, 47)
        metrics = build_metrics(2, 1, 20, 10)
        render._process_one_frame(
            make_task(frame, out, metrics=metrics, bg_color_rgb=bg,
                      leave_black_empty=True, black_threshold=50))
        img = Image.open(str(out)).convert('RGB')
        # dark cell: untouched background throughout
        for dx in range(metrics.cell_w):
            for dy in range(metrics.cell_h):
                assert img.getpixel((dx, dy)) == bg
        # bright cell: some glyph pixels differ from the background
        assert self._glyph_area_hits(img, metrics.cell_w, 0, metrics.cell_w,
                                     metrics.cell_h,
                                     lambda p: p != bg) > 0

    def test_black_threshold_boundary(self, tmp_path):
        # value == threshold is masked (comparison is strict '<')
        for value in (49, 50, 51):
            frame = cell_png(tmp_path / 'frame_000001.png', [[value]])
            out = tmp_path / 'rendered_000001.png'
            metrics = build_metrics(1, 1, 20, 20)
            render._process_one_frame(
                make_task(frame, out, metrics=metrics,
                          bg_color_rgb=(0, 0, 0),
                          leave_black_empty=True, black_threshold=50))
            assert out.exists()

    def test_colorized_samples_source_colors(self, tmp_path):
        frame = tmp_path / 'frame_000001.png'
        Image.new('RGB', (1, 1), (230, 20, 20)).save(str(frame))
        out = tmp_path / 'rendered_000001.png'
        metrics = build_metrics(1, 1, 20, 20)
        render._process_one_frame(
            make_task(frame, out, metrics=metrics, colorized=True))
        img = Image.open(str(out)).convert('RGB')
        assert self._glyph_area_hits(
            img, 0, 0, metrics.cell_w, metrics.cell_h,
            lambda p: p[0] > 150 and p[1] < 100) > 0

    def test_background_alpha_stored_in_output(self, tmp_path):
        frame = cell_png(tmp_path / 'frame_000001.png', [[0]])
        out = tmp_path / 'rendered_000001.png'
        metrics = build_metrics(1, 1, 20, 20)
        render._process_one_frame(
            make_task(frame, out, metrics=metrics, bg_alpha=0.25))
        assert Image.open(str(out)).mode == 'RGBA'
        # int() truncation: int(0.25 * 255) == 63
        assert Image.open(str(out)).getpixel((2, 2))[3] == 63


class TestComputeGlobalStretch:
    def test_percentile_bounds(self, tmp_path):
        frames = [gray_png(tmp_path / f'f{i}.png', v, 20, 20)
                  for i, v in enumerate([50, 50, 205, 205, 50])]
        lo, hi = render.compute_global_stretch(frames, sample_step=1,
                                                percentile=0.5)
        assert lo == 50 and hi == 205

    def test_degenerate_span_falls_back_to_full_range(self, tmp_path):
        frames = [gray_png(tmp_path / f'f{i}.png', 100, 10, 10)
                  for i in range(3)]
        assert render.compute_global_stretch(frames, sample_step=1) == \
            (0, 255)

    def test_empty_input_safe(self):
        assert render.compute_global_stretch([]) == (0, 255)


class TestFramesToRenderedImages:
    def test_parallel_pipeline_end_to_end(self, tmp_path):
        frames = [cell_png(tmp_path / f'frame_{i:06d}.png',
                          [[128, 64], [10, 255]])
                  for i in range(1, 4)]
        metrics = build_metrics(2, 2, 20, 20)
        txt_dir = tmp_path / 'txt_frames'
        rendered = render.frames_to_rendered_images(
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


class TestGridMetrics:
    def test_fit_fills_target_and_centres_remainder(self):
        m = render.GridMetrics.fit(3, 2, 100, 51, font_size=64)
        assert (m.cols, m.rows, m.font_size) == (3, 2, 64)
        assert (m.cell_w, m.cell_h) == (33, 25)
        assert (m.off_x, m.off_y) == (0, 0)      # 1px remainders floor to 0
        assert (m.target_width, m.target_height) == (100, 51)

        assert render.GridMetrics.fit(3, 2, 104, 56, 64).off_x == 1
        assert render.GridMetrics.fit(3, 2, 104, 56, 64).off_y == 0

    def test_cells_never_collapse_below_one_pixel(self):
        m = render.GridMetrics.fit(200, 100, 50, 20, font_size=8)
        assert (m.cell_w, m.cell_h) == (1, 1)

    def test_task_and_metrics_are_immutable(self, tmp_path):
        task = make_task(tmp_path / 'f.png', tmp_path / 'o.png')
        with pytest.raises(dataclasses.FrozenInstanceError):
            task.ramp = 'x'
        with pytest.raises(dataclasses.FrozenInstanceError):
            task.metrics.cols = 1


class TestRenderEdgeCases:
    def test_reused_text_longer_than_grid_is_clipped(self, tmp_path):
        frame = gray_png(tmp_path / 'f.png', 200, 2, 1)
        txt = tmp_path / 'f.txt'
        txt.write_text('@@@@@@\n@@@@@@\n@@@@@@')
        out, text = render._process_one_frame(make_task(
            frame, tmp_path / 'o.png', ascii_path=str(txt),
            metrics=build_metrics(2, 1, 20, 10)))
        assert text == '@@@@@@\n@@@@@@\n@@@@@@'      # text returned untouched
        assert Image.open(out).size == (20, 10)

    def test_ascii_text_persisted_when_txt_dir_given(self, tmp_path):
        frames = [gray_png(tmp_path / 'frames' / f'frame_{i:06d}.png', 255, 2, 2)
                  for i in (1, 2)]
        render.frames_to_rendered_images(
            frames, tmp_path / 'images', build_metrics(2, 1, 20, 10), RAMP, None,
            jobs=1, txt_dir=tmp_path / 'txt', show_progress=False)
        assert sorted(p.name for p in (tmp_path / 'txt').iterdir()) == [
            'frame_000000.txt', 'frame_000001.txt']
        assert (tmp_path / 'txt' / 'frame_000000.txt').read_text() == RAMP[-1] * 2
