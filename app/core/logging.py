import logging

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    logging.basicConfig(level=get_settings().log_level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


logger = structlog.get_logger()
