import requests
import time
import threading
import queue

# ----------------------------
# CONFIG
# ----------------------------

INSTAGRAM_API = "https://www.instagram.com/api/v1/users/web_profile_info/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 Version/16.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
]

REQUEST_INTERVAL = 3.5  # seconds per UA

_request_queue = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

# ----------------------------
# USER AGENT POOL (busy / free)
# ----------------------------

class UserAgentPool:
    def __init__(self, agents, interval):
        self.agents = {
            ua: {"lock": threading.Lock(), "last_used": 0}
            for ua in agents
        }
        self.interval = interval

    def acquire(self):
        while True:
            for ua, meta in self.agents.items():
                if meta["lock"].acquire(blocking=False):
                    now = time.time()
                    wait = self.interval - (now - meta["last_used"])
                    if wait > 0:
                        time.sleep(wait)
                    meta["last_used"] = time.time()
                    return ua, meta["lock"]
            time.sleep(0.1)

UA_POOL = UserAgentPool(USER_AGENTS, REQUEST_INTERVAL)

# ----------------------------
# QUEUE WORKER
# ----------------------------

def _queue_worker():
    while True:
        task = _request_queue.get()
        if task is None:
            break

        func, args, kwargs, result_event = task
        try:
            result_event["result"] = func(*args, **kwargs)
        except Exception as e:
            result_event["result"] = {"exists": None, "status": "error"}
        finally:
            result_event["done"] = True
            _request_queue.task_done()
            time.sleep(REQUEST_INTERVAL)

def _start_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_queue_worker, daemon=True).start()
            _worker_started = True

# ----------------------------
# MAIN VERIFIER
# ----------------------------

def _verify_instagram_internal(username: str, proxy: str = None):
    """
    Returns:
    {
        exists: bool | None,
        status: 'found' | 'not_found' | 'blocked' | 'error'
    }
    """

    ua, lock = UA_POOL.acquire()

    try:
        headers = {
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "129477",
            "Connection": "keep-alive"
        }


        

        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        # 1️⃣ Create session
        session = requests.Session()
        session.headers.update(headers)

        # 2️⃣ Warm-up request (sets cookies like csrftoken, mid)
        session.get("https://www.instagram.com/", timeout=10)

        # 3️⃣ Actual API request
        response = session.get(
            INSTAGRAM_API,
            params={"username": username},
            proxies=proxies,
            timeout=10
        )


        # ----------------------------
        # HARD DECISIONS
        # ----------------------------

        if response.status_code == 404:
            return {"exists": False, "status": "not_found"}

        if response.status_code == 429:
            return {"exists": None, "status": "blocked"}

        if response.status_code != 200:
            return {"exists": None, "status": "error"}

        data = response.json()

        user = data.get("data", {}).get("user")

        if user is None:
            return {"exists": False, "status": "not_found"}

        return {"exists": True, "status": "found"}

    except requests.exceptions.RequestException:
        return {"exists": None, "status": "error"}

    finally:
        lock.release()

def verify_instagram_username(username, proxy=None):
    _start_worker()

    result_event = {"done": False, "result": None}

    _request_queue.put((
        _verify_instagram_internal,
        (username, proxy),
        {},
        result_event
    ))

    while not result_event["done"]:
        time.sleep(0.01)

    return result_event["result"]

# ----------------------------
# LOCAL TEST
# ----------------------------

if __name__ == "__main__":
    test_usernames = ["thisacoountsoeenotexist"]

    for u in test_usernames:
        result = verify_instagram_username(u)
        print(f"Verification for '{u}': {result}")
