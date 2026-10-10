"""Architecture guards: banned APIs and the CLI/core separation contract."""

import ast
from pathlib import Path

import asciify_video
from asciify_video import (
    cli,
    config,
    ffmpeg,
    pipeline,
    reuse,
    state,
)

PACKAGE_DIR = Path(asciify_video.__file__).parent


def package_trees(exclude=()):
    """Yield (filename, ast) for every module in the package.

    Syntax errors surface as test errors, never silent passes.
    """
    for path in sorted(PACKAGE_DIR.glob('*.py')):
        if path.name not in exclude:
            yield path.name, ast.parse(path.read_text(encoding='utf-8'))


class TestNoDeprecatedPillowUsage:
    def test_getdata_not_used_in_source(self):
        """Image.getdata() is deprecated and slated for removal in
        Pillow 14. Walks every package module's syntax tree and fails
        if any attribute named 'getdata' is referenced — comments and
        docstrings (including those explaining this prohibition)
        can never trigger it. Use _flattened_pixels() or
        Image.tobytes() instead.
        """
        offenders = [
            f'{name} line {node.lineno}'
            for name, tree in package_trees()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == 'getdata'
        ]
        assert not offenders, (
            f'getdata() is deprecated (Pillow 14 removal); '
            f'found attribute access at {offenders}; '
            f'use _flattened_pixels() or Image.tobytes() instead')


class TestCliCoreSeparation:
    """The refactor's central contract: no CLI machinery in the core,
    no video processing logic in the CLI."""

    # Modules allowed to contain CLI machinery
    CLI_MODULES = ('cli.py', '__main__.py')

    def test_core_modules_have_no_argparse_import(self):
        """argparse belongs exclusively to the CLI layer."""
        for name, tree in package_trees(exclude=self.CLI_MODULES):
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(alias.name != 'argparse'
                               for alias in node.names), \
                        f'{name} imports argparse; move it to cli.py'
                elif isinstance(node, ast.ImportFrom):
                    assert node.module != 'argparse', \
                        f'{name} imports argparse; move it to cli.py'

    def test_core_modules_define_no_main(self):
        """Programmatic entry is run_conversion(); main() lives in the
        CLI."""
        offenders = [
            f'{name} line {node.lineno}'
            for name, tree in package_trees(exclude=self.CLI_MODULES)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == 'main']
        assert not offenders, \
            f'main() defined at {offenders}; the CLI entry ' \
            f'point belongs in cli.py'

    def test_core_entry_points_exist(self):
        assert asciify_video.run_conversion is pipeline.run_conversion
        assert asciify_video.ConversionConfig is config.ConversionConfig
        assert callable(pipeline.run_conversion)
        # state / reuse / ffmpeg helpers remain core-owned
        for module, names in (
                (state, ('load_state', 'save_state', 'report_state')),
                (reuse, ('resolve_reuse_dir', 'validate_reuse')),
                (ffmpeg, ('probe_video', 'chromakey_to_black',
                          'extract_frames', 'assemble_video'))):
            for name in names:
                assert callable(getattr(module, name)), name

    def test_cli_entry_point_exists(self):
        assert callable(cli.main)
        assert callable(cli.build_parser)
        assert callable(cli.build_config)

    def test_cli_parser_roundtrips_all_options(self):
        """Every flag parses; spot-check a few values survive intact."""
        parser = cli.build_parser()
        args = parser.parse_args([
            'in.mp4', 'out.mp4', '--key-color', '0x00B140',
            '--ascii-width', '480', '--no-key', '--leave-black-empty',
            '--background-alpha', '0.5', '--font-color', '#FF0000',
            '--reuse', 'final', '--jobs', '4', '--vpx-preset', 'slow'])
        assert args.input == 'in.mp4' and args.output == 'out.mp4'
        assert args.key_color == '0x00B140'
        assert args.ascii_width == 480
        assert args.no_key and args.leave_black_empty
        assert args.background_alpha == 0.5
        assert args.font_color == '#FF0000'
        assert args.reuse == 'final'
        assert args.jobs == 4
        assert args.vpx_preset == 'slow'
