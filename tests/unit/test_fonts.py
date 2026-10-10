"""Monospace font discovery and fallbacks."""

from PIL import ImageFont

from asciify_video import fonts


class TestLoadMonospaceFont:
    def test_returns_a_font_that_can_measure_text(self):
        font = fonts.load_monospace_font(24)
        left, top, right, bottom = font.getbbox('M')
        assert right > left and bottom > top

    def test_falls_back_to_builtin_font_when_no_truetype_found(self, monkeypatch):
        def unavailable(*args, **kwargs):
            raise OSError('cannot open resource')
        monkeypatch.setattr(ImageFont, 'truetype', unavailable)
        monkeypatch.setattr(fonts.glob, 'glob', lambda *a, **k: ['/fake/Font.ttf'])

        sentinel = object()
        monkeypatch.setattr(ImageFont, 'load_default', lambda size=None: sentinel)
        assert fonts.load_monospace_font(24) is sentinel

    def test_old_pillow_without_sized_default_font(self, monkeypatch):
        monkeypatch.setattr(ImageFont, 'truetype',
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        monkeypatch.setattr(fonts.glob, 'glob', lambda *a, **k: [])
        sentinel = object()

        def legacy_load_default(*args, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword argument 'size'")
            return sentinel
        monkeypatch.setattr(ImageFont, 'load_default', legacy_load_default)
        assert fonts.load_monospace_font(24) is sentinel
