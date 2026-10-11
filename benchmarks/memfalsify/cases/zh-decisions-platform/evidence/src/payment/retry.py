from .settings import RETRY_TIMES


def should_retry(endpoint, attempt: int) -> bool:
    """扣款、退款等非幂等接口直接返回 False，避免重复记账。"""
    if not endpoint.idempotent:
        return False
    return attempt < RETRY_TIMES
