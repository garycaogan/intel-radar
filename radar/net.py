"""HTTP helpers: plain GET/POST with retry, the TikHub client and a call ledger.

Only the standard library, so the daily job runs from cron without a venv.
Every paid call (TikHub, the model) goes through ``ledger`` so a run can report
what it spent.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from . import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


class Ledger:
    """Counts calls per provider for the run report."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self.tokens: dict[str, list[int]] = {}
        self.usd: dict[str, float] = {}       # TikHub spend per endpoint

    def charge(self, path: str) -> None:
        price = config.TIKHUB_PRICES.get(path, config.TIKHUB_USD_PER_CALL)
        self.usd[path] = round(self.usd.get(path, 0) + price, 4)

    def hit(self, name: str, ok: bool = True) -> None:
        bucket = self.calls if ok else self.failures
        bucket[name] = bucket.get(name, 0) + 1

    def add_tokens(self, model: str, prompt: int, completion: int) -> None:
        cur = self.tokens.setdefault(model, [0, 0])
        cur[0] += prompt
        cur[1] += completion

    def snapshot(self) -> dict:
        return {"calls": dict(self.calls), "failures": dict(self.failures), "usd": dict(self.usd),
                "tokens": {k: list(v) for k, v in self.tokens.items()}}

    def summary(self, since: dict | None = None) -> dict:
        """Spend so far, or since a ``snapshot()`` (the deep dive runs in the same process as the daily run)."""
        s = since or {"calls": {}, "failures": {}, "usd": {}, "tokens": {}}
        calls = {k: v - s["calls"].get(k, 0) for k, v in self.calls.items() if v - s["calls"].get(k, 0)}
        failures = {k: v - s["failures"].get(k, 0) for k, v in self.failures.items() if v - s["failures"].get(k, 0)}
        usd = {k: round(v - s["usd"].get(k, 0), 4) for k, v in self.usd.items() if v - s["usd"].get(k, 0) > 1e-9}
        tokens = {m: [p - s["tokens"].get(m, [0, 0])[0], c - s["tokens"].get(m, [0, 0])[1]]
                  for m, (p, c) in self.tokens.items()}
        tokens = {m: t for m, t in tokens.items() if any(t)}
        llm_cny = 0.0
        for model, (p, c) in tokens.items():
            pin, pout = config.LLM_PRICE_CNY_PER_M
            llm_cny += p / 1e6 * pin + c / 1e6 * pout
        return {
            "calls": calls,
            "failures": failures,
            "tokens": tokens,
            "est_usd_tikhub": round(sum(usd.values()), 3),
            "tikhub_by_endpoint": {k.rsplit("/", 2)[-2] + "/" + k.rsplit("/", 1)[-1]: v for k, v in usd.items()},
            "est_cny_llm": round(llm_cny, 2),
        }


ledger = Ledger()

_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def _read(resp) -> bytes:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw


def request(url: str, *, params: dict | None = None, headers: dict | None = None,
            body: dict | None = None, timeout: float = 30, retries: int = 2,
            name: str = "http") -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    last = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with _opener.open(req, timeout=timeout) as resp:
                out = _read(resp)
            ledger.hit(name)
            return out
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (400, 401, 403, 404) and name != "tikhub":
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = type(exc).__name__
        time.sleep(1.5 * (attempt + 1))
    ledger.hit(name, ok=False)
    raise FetchError(f"{name} {last}: {url[:120]}")


def get_json(url: str, **kw):
    return json.loads(request(url, **kw).decode("utf-8", "replace"))


def get_text(url: str, **kw) -> str:
    return request(url, **kw).decode("utf-8", "replace")


# ------------------------------------------------------------------ TikHub

def tikhub(path: str, params: dict, timeout: float = 60, post: bool = False) -> dict:
    """GET (or POST JSON, for the newer search endpoints) a TikHub endpoint. Billed per call at the
    endpoint's price (config.TIKHUB_PRICES); the unbilled 400 "please retry" is retried.
    A run stops calling once it reaches ``config.TIKHUB_MAX_CALLS``."""
    if ledger.calls.get("tikhub", 0) + ledger.failures.get("tikhub", 0) >= config.TIKHUB_MAX_CALLS \
            or sum(ledger.usd.values()) >= config.TIKHUB_MAX_USD:
        raise FetchError("TikHub budget for this run used up")
    key = config.TIKHUB_API_KEY
    if not key:
        raise FetchError("TIKHUB_API_KEY is not set")
    url = config.TIKHUB_BASE_URL + path + ("" if post else "?" + urllib.parse.urlencode(params))
    headers = {"Authorization": "Bearer " + key, "User-Agent": UA}
    data = None
    if post:
        data = json.dumps(params).encode()
        headers["Content-Type"] = "application/json"
    last = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(_read(resp).decode("utf-8", "replace"))
            ledger.hit("tikhub")
            ledger.charge(path)
            if out.get("code") not in (200, None):
                raise FetchError(f"TikHub code {out.get('code')}: {str(out.get('message'))[:120]}")
            return out.get("data") or {}
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code not in (400, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
        time.sleep(2 * (attempt + 1))
    ledger.hit("tikhub", ok=False)
    raise FetchError(f"TikHub {last}: {path}")
