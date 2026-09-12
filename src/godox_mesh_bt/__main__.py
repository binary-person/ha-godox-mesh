"""Module entrypoint for ``python -m godox_mesh_bt``.

Examples
--------
>>> callable(run)
True
"""

from __future__ import annotations

import logging

from godox_mesh_bt.cli import run

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.debug("starting CLI entrypoint")
    run()
