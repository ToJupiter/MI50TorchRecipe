from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-24s %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(debug: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    logging.getLogger("torch").setLevel(logging.WARNING)
    logging.getLogger("torch._inductor").setLevel(logging.WARNING)
