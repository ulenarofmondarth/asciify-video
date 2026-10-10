asciify-video
=============

Convert green-screen video into ASCII-art video, with consistent character
geometry, multi-core rendering, optional transparency, and fast iteration
through artifact reuse.

.. code-block:: bash

   pip install asciify-video
   asciify_video input.mp4 output.mp4

``ffmpeg`` and ``ffprobe`` must be installed separately; see
:doc:`user/installation`. The source lives at
`https://github.com/ulenarofmondarth/asciify-video <https://github.com/ulenarofmondarth/asciify-video>`_.

.. toctree::
   :maxdepth: 2
   :caption: User guide

   user/installation
   user/quickstart
   user/appearance
   user/reuse-workflow
   user/cli-reference
   user/python-usage
   user/troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Design

   design/architecture
   design/pipeline
   design/rendering
   design/state-file
   design/decisions

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api/index
   changelog

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
