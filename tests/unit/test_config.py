"""ConversionConfig defaults."""

from pathlib import Path

import pytest

from asciify_video import config, constants


class TestConversionConfig:
    def test_defaults_match_constants(self):
        cfg = config.ConversionConfig(input=Path('a.mp4'),
                                       output=Path('b.mp4'))
        assert cfg.no_key is False
        assert cfg.key_color is None
        assert cfg.tolerance == constants.DEFAULT['TOLERANCE']
        assert cfg.ascii_width is None       # derived from the output width
        assert cfg.scale_divisor == constants.DEFAULT['SCALE-DIVISOR'] == 8
        assert cfg.background_alpha == 1.0
        assert cfg.font_color_rgb == (0x64, 0xFF, 0x64)
        assert cfg.background_rgb == (0, 0, 0)
        assert cfg.reuse_level is None and cfg.reuse_dir is None
        assert cfg.target_resolution is None
        assert cfg.vpx_preset == 'ultrafast'


class TestColorAsRgb:
    @pytest.mark.parametrize('text, expected', [
        ('#64FF64', (0x64, 0xFF, 0x64)),
        ('000000', (0, 0, 0)),
        ('#aabbcc', (0xAA, 0xBB, 0xCC)),
    ])
    def test_parses_hex(self, text, expected):
        assert config.color_as_rgb(text) == expected

    @pytest.mark.parametrize('text', ['', '#12', 'green', '#GGHHII'])
    def test_rejects_malformed(self, text):
        with pytest.raises(ValueError):
            config.color_as_rgb(text)
