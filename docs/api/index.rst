API reference
=============

The supported programmatic entry points are re-exported from the top-level
package: build a :class:`~asciify_video.config.ConversionConfig` and pass it
to :func:`~asciify_video.pipeline.run_conversion`. The remaining modules are
documented for contributors and for callers who want a single stage.

.. automodule:: asciify_video
   :no-members:

.. toctree::
   :maxdepth: 1

   config
   pipeline
   render
   keying
   ffmpeg
   fonts
   reuse
   state
   constants
   cli
