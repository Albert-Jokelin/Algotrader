import time
import logging
from typing import Callable, Any
import requests
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

logger = logging.getLogger("upstoxlite")

def default_backoff(retries=5):
    return retry(
        retry=retry_if_exception_type((requests.exceptions.RequestException,)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
        stop=stop_after_attempt(retries),
        reraise=True,
    )

def parse_retry_after(resp):
    header = resp.headers.get("Retry-After")
    if not header:
        return None
    try:
        return int(header)
    except:
        return None
