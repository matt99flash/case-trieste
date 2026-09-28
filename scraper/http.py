"""Client HTTP 'educato': imita un browser, rispetta una pausa minima per sito, riprova in caso di errori."""
import threading
import time
from urllib.parse import urlparse

from curl_cffi import requests as creq

_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_global = threading.Lock()

HEADERS = {
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
}


class FetchError(Exception):
    pass


class Http:
    def __init__(self, min_delay: float = 1.0, timeout: int = 30, retries: int = 3):
        self.min_delay = min_delay
        self.timeout = timeout
        self.retries = retries
        self.session = creq.Session(impersonate="chrome", headers=HEADERS)
        self.requests = 0

    def _wait(self, host: str):
        with _global:
            lock = _host_locks.setdefault(host, threading.Lock())
        with lock:
            wait = _host_last.get(host, 0) + self.min_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _host_last[host] = time.monotonic()

    def request(self, method: str, url: str, **kw):
        host = urlparse(url).netloc
        last = None
        for attempt in range(self.retries):
            self._wait(host)
            try:
                self.requests += 1
                r = self.session.request(method, url, timeout=self.timeout, allow_redirects=True, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = FetchError(f"HTTP {r.status_code} {url}")
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"HTTP {r.status_code} {url}")
                return r
            except FetchError:
                raise
            except Exception as e:  # errori di rete
                last = FetchError(f"{type(e).__name__}: {e} ({url})")
                time.sleep(3 * (attempt + 1))
        raise last

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def text(self, url, **kw) -> str:
        r = self.get(url, **kw)
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
            # molti siti dichiarano male la codifica: prova utf-8
            try:
                return r.content.decode("utf-8")
            except UnicodeDecodeError:
                return r.content.decode("cp1252", errors="replace")
        return r.text

    def json(self, url, **kw):
        return self.get(url, **kw).json()
