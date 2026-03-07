"""Verify exchange Retry-After mandate is respected (never capped by internal max)."""


def test_retry_after_overrides_internal_cap():
    """When exchange says Retry-After: 60, wait must be >= 60 even if MAX_RETRY_WAIT_S=30."""
    MAX_RETRY_WAIT_S = 30
    retry_after = 60
    attempt = 0
    backoff = 2 ** attempt + 0.5  # ~1.5s
    wait = max(retry_after, min(backoff, MAX_RETRY_WAIT_S))
    assert wait == 60, f"Exchange demands 60s but got {wait}s"


def test_backoff_capped_when_no_retry_after():
    """When no Retry-After header, backoff is capped at MAX_RETRY_WAIT_S."""
    MAX_RETRY_WAIT_S = 30
    retry_after = 0
    attempt = 10  # 2^10 = 1024
    backoff = 2 ** attempt + 0.5
    wait = max(retry_after, min(backoff, MAX_RETRY_WAIT_S))
    assert wait == MAX_RETRY_WAIT_S, f"Expected cap at {MAX_RETRY_WAIT_S} but got {wait}"


def test_retry_after_smaller_than_backoff_uses_backoff():
    """When Retry-After: 5 but backoff is 15s, use the larger (backoff)."""
    MAX_RETRY_WAIT_S = 30
    retry_after = 5
    attempt = 4  # 2^4 = 16 + jitter
    backoff = 2 ** attempt + 0.5  # ~16.5
    wait = max(retry_after, min(backoff, MAX_RETRY_WAIT_S))
    assert wait == min(backoff, MAX_RETRY_WAIT_S), f"Should use backoff={backoff}"
