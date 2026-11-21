from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone


class RateLimiter:
    def __init__(self) -> None:
        self.login_failures: dict[str, deque[datetime]] = defaultdict(deque)
        self.login_blocked_until: dict[str, datetime] = {}
        self.register_attempts: dict[str, deque[datetime]] = defaultdict(deque)

    def login_wait_seconds(self, key: str) -> int:
        until = self.login_blocked_until.get(key)
        if not until:
            return 0
        remaining = int((until - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            self.login_blocked_until.pop(key, None)
            self.login_failures.pop(key, None)
            return 0
        return remaining

    def login_failed(self, key: str) -> int:
        now = datetime.now(timezone.utc)
        failures = self.login_failures[key]
        while failures and (now - failures[0]).total_seconds() > 300:
            failures.popleft()
        failures.append(now)
        if len(failures) >= 10:
            self.login_blocked_until[key] = now + timedelta(minutes=5)
            return 300
        return 0

    def login_succeeded(self, key: str) -> None:
        self.login_failures.pop(key, None)
        self.login_blocked_until.pop(key, None)

    def register_allowed(self, key: str) -> tuple[bool, int]:
        now = datetime.now(timezone.utc)
        attempts = self.register_attempts[key]
        while attempts and (now - attempts[0]).total_seconds() >= 3600:
            attempts.popleft()
        if len(attempts) >= 10:
            return False, max(1, int(3600 - (now - attempts[0]).total_seconds()))
        attempts.append(now)
        return True, 0


rate_limiter = RateLimiter()
