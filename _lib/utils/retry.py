"""utils/retry.py -- shared exponential-backoff retry for transient data_access failures."""

import logging
import random
import time

logger = logging.getLogger(__name__)


def retry(fn, *, max_attempts=3, base_delay=1.0, jitter=0.2):
    """Exponential backoff with jitter for transient data_access failures."""
    attempts = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempts += 1
            if attempts >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempts - 1))
            delay *= (1 + random.uniform(-jitter, jitter))
            logger.warning(
                "Data access error attempt %d, retrying in %.1fs: %s",
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
