import time
from typing import Dict, Tuple, Optional


class RateLimiter:
    def __init__(self, max_requests: int, period_seconds: int):
        self.max_requests = max_requests
        self.period_seconds = period_seconds
        self.requests: Dict[str, list] = {}

    def check_and_consume(self, key: str) -> Tuple[bool, Optional[str]]:
        """检查并消耗一个请求名额"""
        current_time = time.time()
        
        if key not in self.requests:
            self.requests[key] = []
        
        # 清理过期的请求记录
        self.requests[key] = [t for t in self.requests[key] if current_time - t < self.period_seconds]
        
        if len(self.requests[key]) >= self.max_requests:
            remaining_time = self.period_seconds - (current_time - min(self.requests[key]))
            return False, f"请求过于频繁，请等待 {int(remaining_time)} 秒后再试"
        
        # 记录新请求
        self.requests[key].append(current_time)
        return True, None
