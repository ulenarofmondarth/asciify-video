"""Sphinx configuration for the asciify-video documentation."""

import os
import sys
from importlib.metadata import PackageNotFoundError, version

# Lets autodoc import the package from a source checkout that is not installed.
sys.path.insert(0, os.path.abspath('../src'))

project = 'asciify-video'
author = 'Mark Bools'
copyright = '2026, Mark Bools'  # noqa: A001 - name required by Sphinx

try:
    release = version('asciify-video')
except PackageNotFoundError:
    release = '0.0.0'
version = '.'.join(release.split('.')[:2])

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',      # Google-style docstrings
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'myst_parser',              # Markdown guides and the changelog
    'sphinxarg.ext',            # CLI reference generated from the argument parser
]

source_suffix = {'.rst': 'restructuredtext', '.md': 'markdown'}
exclude_patterns = ['_build']

# -- autodoc -----------------------------------------------------------------
autodoc_member_order = 'bysource'
autodoc_typehints = 'description'
autodoc_default_options = {'members': True, 'show-inheritance': True}
# Show the project's alias names in signatures instead of expanding them.
autodoc_type_aliases = {
    'RGB': 'asciify_video.config.RGB',
    'State': 'asciify_video.state.State',
    'Stretch': 'asciify_video.render.Stretch',
    'StrPath': 'asciify_video.ffmpeg.StrPath',
    'Size': 'asciify_video.pipeline.Size',
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'PIL': ('https://pillow.readthedocs.io/en/stable/', None),
}

# -- MyST --------------------------------------------------------------------
myst_heading_anchors = 3

# -- HTML --------------------------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_title = f'{project} {release}'
html_static_path = ['_static']
