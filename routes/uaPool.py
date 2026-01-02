import time
import threading

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 Version/16.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
]

MIN_INTERVAL = 3  # seconds per UA


class UAPool:
    def __init__(self, user_agents):
        self.lock = threading.Lock()
        self.pool = {
            ua: 0 for ua in user_agents  # last_used_timestamp
        }

    def acquire(self):
        """
        Blocks until a UA is available
        """
        while True:
            with self.lock:
                now = time.time()
                for ua, last_used in self.pool.items():
                    if now - last_used >= MIN_INTERVAL:
                        self.pool[ua] = now
                        return ua
            time.sleep(0.1)  # wait and retry
