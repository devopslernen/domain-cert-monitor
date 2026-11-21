"""Entry point: load settings, configure logging, run uvicorn with one worker."""

import logging
import sys

import uvicorn

from .app import create_app
from .config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format=LOG_FORMAT, stream=sys.stdout, force=True
    )
    app = create_app(settings)
    # One worker: the scheduler and the in-memory locks live in this process.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        workers=1,
        log_config=None,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
