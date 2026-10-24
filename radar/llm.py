"""The judgment layer: any OpenAI-compatible chat completions endpoint (config.LLM_*).

The model only groups, names and comments; every number and label is computed in code. Replies are
asked for as JSON. Prefer a model without long thinking for these prompts: with thinking on, long
prompts can run into a gateway's timeout (set LLM_EXTRA_BODY to switch it off where the API allows).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import config, net


class LLMError(RuntimeError):
    pass


def _key() -> str:
    if not config.LLM_API_KEY:
        raise LLMError("LLM_API_KEY is not set (see .env.example)")
    return config.LLM_API_KEY


def chat(prompt: str, model: str = "", system: str = "", max_tokens: int = 12000,
         timeout: float | None = None) -> str:
    key = _key()
    model = model or config.LLM_MAIN
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3, **config.LLM_EXTRA_BODY}
    last = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(config.LLM_BASE_URL + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                  "User-Agent": net.UA})
            with urllib.request.urlopen(req, timeout=timeout or config.LLM_TIMEOUT) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
            usage = out.get("usage") or {}
            net.ledger.add_tokens(model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
            net.ledger.hit("llm")
            text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if text.strip():
                return text
            last = "empty reply"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace').replace(key, '[key]')[:200]}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = type(exc).__name__
        net.ledger.hit("llm", ok=False)
        time.sleep(5)
    raise LLMError(f"{model}: {last}")


def extract_json(text: str):
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    starts = [i for i in (candidate.find("{"), candidate.find("[")) if i >= 0]
    if not starts:
        raise LLMError("no JSON in reply")
    return json.JSONDecoder().raw_decode(candidate[min(starts):])[0]


def ask_json(prompt: str, model: str = "", max_tokens: int = 12000, system: str = ""):
    """A reply that isn't JSON gets one more try."""
    for attempt in range(2):
        text = chat(prompt, model=model, max_tokens=max_tokens, system=system)
        try:
            return extract_json(text)
        except (ValueError, LLMError):
            if attempt:
                raise LLMError("reply was not JSON twice: " + text[:200])
