"""
Periodic LLM feedback backfill scheduler.

Runs `scripts/backfill_feedback_with_llm.run_backfill_once` on a fixed interval.
"""

from __future__ import annotations

import asyncio
import logging
import os

from scripts.backfill_feedback_with_llm import run_backfill_once

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    raw = os.getenv("ENABLE_LLM_FEEDBACK_BACKFILL", "true").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _interval_seconds() -> int:
    raw = os.getenv("LLM_FEEDBACK_BACKFILL_INTERVAL_SECONDS", "300").strip()
    try:
        val = int(raw)
        return max(60, val)
    except ValueError:
        return 300


async def periodic_feedback_backfill(stop_event: asyncio.Event) -> None:
    """
    Background loop that periodically updates feedback rows using the LLM wrapper.
    """
    if not _is_enabled():
        logger.info("LLM feedback backfill scheduler is disabled.")
        return

    interval = _interval_seconds()
    logger.info("LLM feedback backfill scheduler started (interval=%ss).", interval)

    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(run_backfill_once)
            logger.info(
                "LLM feedback backfill pass finished: inserted=%s updated=%s skipped=%s",
                result.get("inserted"),
                result.get("updated"),
                result.get("skipped"),
            )
        except Exception as e:
            logger.error("LLM feedback backfill pass failed: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue

    logger.info("LLM feedback backfill scheduler stopped.")

