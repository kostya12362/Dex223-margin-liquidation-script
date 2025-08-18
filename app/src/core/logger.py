import sys
import logging
from pathlib import Path
from typing import Union

from loguru import logger

from config import settings

LOG_FORMAT = ("<green>{time:YYYY-MM-DD HH:mm:ss:SSS}</green> | <level>{level: <8}</level> | "
              "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")


class InterceptHandler(logging.Handler):
    """Logging handler for intercepting standard logging messages."""

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logger(
        file_path: Path | None = settings.LOG_PATH / "{time}.log",
        *,
        level: Union[str, int] = "DEBUG" if settings.DEBUG else settings.LOG_LEVEL,
        log_format: str = LOG_FORMAT,
        rotation: str = "02:00",
        retention: str = "7 days",
        compression: str = "zip",
        backtrace: bool = True,
        diagnose: bool = True,
):
    """Configuration Loguru for integration with logging."""
    logger.remove()
    intercept_handler = InterceptHandler()
    if file_path:
        settings.LOG_PATH.mkdir(parents=True, exist_ok=True)
        logger.add(
            file_path,
            format=log_format,
            level="DEBUG",
            enqueue=True,
            rotation=rotation,
            retention=retention,
            compression=compression,
            backtrace=backtrace,
            diagnose=diagnose,
        )
    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    logging.root.handlers = [intercept_handler]
    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        colorize=True,
        backtrace=backtrace,
        diagnose=diagnose,
    )
