import logging
from .config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
