Command-line reference
======================

This page is generated from the argument parser, so it always matches the
installed version. ``asciify_video --help`` prints the same information.

.. argparse::
   :module: asciify_video.cli
   :func: build_parser
   :prog: asciify_video

Exit status
-----------

==========  ==================================================================
Status      Meaning
==========  ==================================================================
0           Conversion finished, or ``--report`` / ``--state-file`` printed.
1           Input file missing, an option value was invalid, no usable reuse
            directory was found, or the conversion failed or was interrupted.
2           Invalid usage, reported by the argument parser.
==========  ==================================================================

Environment variables
---------------------

``ASCII_VIDEO_TEMP``
    Directory to reuse with ``--reuse`` when ``--reuse-dir`` is not given.
    Takes priority over the directory recorded in the state file.

``XDG_CACHE_HOME``
    Base directory for the state file, which is stored at
    ``$XDG_CACHE_HOME/asciify/state.json``. Defaults to ``~/.cache``.

Options that do nothing when reusing
------------------------------------

Each ``--reuse`` level skips the stages that produced the artifacts it
starts from, so the options feeding those stages have no effect. ``asciify_video``
lists them at startup and flags any you passed. See :doc:`reuse-workflow`.
