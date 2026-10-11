"""支付服务配置。"""

# 2026-09-12 事故后由 3 秒放宽到 8 秒，降低大促时误判超时的可能性
PAYMENT_TIMEOUT_SECONDS = 8

# 连接池上限，事故前为 20
DB_POOL_SIZE = 80

# 非幂等接口（如扣款）不重试，见 retry.py
RETRY_TIMES = 2
