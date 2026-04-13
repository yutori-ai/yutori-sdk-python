"""Shared helpers for runnable example scripts."""

from __future__ import annotations

import argparse
import sys

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{file}</cyan>:<cyan>{line:>3}</cyan> | "
    "<level>{message}</level>{exception}"
)


def configure_example_logging() -> None:
    logger.remove()
    logger.level("DEBUG", color="<fg #808080>")
    logger.add(sys.stdout, format=_LOG_FORMAT, colorize=True)


def add_task_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--task", default=default_config.task, help="The task to perform")
    parser.add_argument("--start-url", default=default_config.start_url, help="Starting URL")


def add_model_arguments(parser: argparse.ArgumentParser, default_config, *, api_label: str) -> None:
    parser.add_argument("--base-url", default=default_config.base_url, help=f"{api_label} base URL")
    parser.add_argument("--model", default=default_config.model, help=f"{api_label} model")
    parser.add_argument(
        "--temperature",
        type=float,
        default=default_config.temperature,
        help=f"{api_label} temperature",
    )


def add_agent_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--max-steps", type=int, default=default_config.max_steps, help="Maximum number of steps")


def add_browser_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--viewport-width", type=int, default=default_config.viewport_width, help="Viewport width")
    parser.add_argument("--viewport-height", type=int, default=default_config.viewport_height, help="Viewport height")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")


def add_payload_trim_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=default_config.max_request_bytes,
        help="Max payload size in bytes before trimming old screenshots",
    )
    parser.add_argument(
        "--keep-recent-screenshots",
        type=int,
        default=default_config.keep_recent_screenshots,
        help="Number of recent screenshots to protect from trimming",
    )


def add_replay_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument(
        "--replay-dir",
        default=default_config.replay_dir,
        help="Optional directory for replay artifacts",
    )
    parser.add_argument("--replay-id", default=default_config.replay_id, help="Optional replay run id")
