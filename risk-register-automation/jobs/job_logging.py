"""Structured logging setup for scheduled jobs.

Provides:
- JSON-formatted log output (for machine parsing)
- Human-readable console output
- File logging with rotation
- Job metrics tracking (execution time, counts, errors)

Usage:
    from jobs.job_logging import setup_logging, log_job_metrics

    logger = setup_logging("daily_risk_check")
    logger.info("Starting job...")
    log_job_metrics(logger, {"risks": 92, "sent": 5}, start_time)
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


# Log directory
LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "job": getattr(record, "job_name", "unknown"),
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class ConsoleFormatter(logging.Formatter):
    """Human-readable format for console output."""

    LEVEL_SYMBOLS = {
        "DEBUG": "🔍",
        "INFO": "ℹ️ ",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "CRITICAL": "🚨",
    }

    def format(self, record):
        symbol = self.LEVEL_SYMBOLS.get(record.levelname, "  ")
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"{timestamp} {symbol} {record.getMessage()}"


def setup_logging(job_name: str, log_level: str = None) -> logging.Logger:
    """Configure logging for a job.

    Sets up:
    - Console handler (human-readable)
    - File handler (JSON structured, with rotation)

    Args:
        job_name: Name of the job (used in log filenames and JSON).
        log_level: Override log level (default: INFO, or LOG_LEVEL env var).

    Returns:
        Configured logger instance.
    """
    level_str = log_level or os.environ.get("LOG_LEVEL", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)

    logger = logging.getLogger(job_name)
    logger.setLevel(level)

    # Clear any existing handlers
    logger.handlers.clear()

    # Add job_name to all records
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.job_name = job_name
        return record

    logging.setLogRecordFactory(record_factory)

    # Console handler (human-readable)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    # File handler (JSON, rotated at 10MB, keep 5 backups)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{job_name}.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    return logger


def log_job_metrics(logger: logging.Logger, metrics: dict, start_time: float) -> None:
    """Log final job metrics as a structured JSON line.

    Args:
        logger: The job logger.
        metrics: Dict of metrics to log.
        start_time: time.time() when the job started.
    """
    elapsed = time.time() - start_time
    metrics["duration_seconds"] = round(elapsed, 2)
    metrics["finished_at"] = datetime.utcnow().isoformat() + "Z"

    logger.info(f"JOB_METRICS: {json.dumps(metrics)}")
    logger.info(f"Job completed in {elapsed:.1f}s")
