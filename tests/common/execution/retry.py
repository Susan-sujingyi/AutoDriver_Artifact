"""Method-neutral bounded retry decisions; only pre-delivery transport failures."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import time

POLICY = 'autodriver.request-retry/v1'
BACKOFF = (2.0, 5.0)


def delay(diagnostic, attempt, *, now=None):
    """Zero-based attempt: at most two retries; None means do not retry."""
    if attempt >= len(BACKOFF) or diagnostic.get('phase') not in ('upstream_headers', 'upstream_body', 'upstream_terminal'):
        return None
    if diagnostic.get('reason') in ('Episode deadline reached', 'Request-count limit reached',
                                   'Insufficient episode budget for the next request reservation'):
        return None
    if diagnostic.get('provider_error_code') in ('insufficient_quota', 'insufficient_balance',
                                                 'invalid_api_key', 'authentication_error'):
        return None
    status = diagnostic.get('http_status')
    transient = (diagnostic.get('provider_error_code') in ('server_error', 'overloaded', 'rate_limit_exceeded') or
                 status in (408, 429, 500, 502, 503, 504) or
                 status is None and diagnostic.get('error_type') in
                 ('TimeoutError', 'ConnectionError', 'ConnectionResetError',
                  'ConnectionAbortedError', 'RemoteDisconnected', 'IncompleteRead', 'URLError'))
    if not transient:
        return None
    wait = BACKOFF[attempt]
    value = diagnostic.get('retry_after')
    if value is not None:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = date.timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, OverflowError):
                seconds = 0
        if math.isfinite(seconds):
            wait = max(wait, seconds)
    return wait
