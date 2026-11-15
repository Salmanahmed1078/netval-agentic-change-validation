"""LLM back-ends: local (Ollama) and hosted (Anthropic API), with a disk cache.

Every call is cached on disk keyed by (model, system, user, seed), which makes
all experiments exactly re-playable (Phase 2, Step 7: "all prompts, seeds and
logs are saved").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Optional, Tuple

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "llm_cache")


def _env():
    try:
        from dotenv import dotenv_values
        return dotenv_values(os.path.join(ROOT, ".env"))
    except Exception:
        return {}


def extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


class LLM:
    name = "base"
    price_in = 0.0     # $ per 1M tokens
    price_out = 0.0

    def __init__(self):
        self._lock = threading.Lock()

    def _key(self, system, user, seed):
        h = hashlib.sha256(json.dumps([self.name, system, user, seed]).encode()).hexdigest()[:24]
        d = os.path.join(CACHE, re.sub(r"[^a-zA-Z0-9.-]", "_", self.name))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, h + ".json")

    def chat(self, system: str, user: str, seed: int = 1) -> Tuple[Optional[dict], dict]:
        path = self._key(system, user, seed)
        if os.path.exists(path):
            rec = json.load(open(path))
            rec["meta"]["cached"] = True
            return extract_json(rec["text"]), rec["meta"]
        t0 = time.time()
        text, tin, tout = self._call(system, user, seed)
        meta = {"latency": round(time.time() - t0, 2), "in_tokens": tin, "out_tokens": tout,
                "cost_usd": (tin * self.price_in + tout * self.price_out) / 1e6, "model": self.name, "cached": False}
        with open(path, "w") as f:
            json.dump({"system": system, "user": user, "seed": seed, "text": text, "meta": meta}, f)
        return extract_json(text), meta

    def _call(self, system, user, seed):  # pragma: no cover
        raise NotImplementedError


class OllamaLLM(LLM):
    def __init__(self, model: str = None, url: str = None):
        super().__init__()
        e = _env()
        self.model = model or e.get("LOCAL_MODEL") or "qwen2.5:7b-instruct"
        self.url = url or e.get("OLLAMA_URL") or "http://localhost:11434"
        self.name = f"local:{self.model}"

    def _call(self, system, user, seed):
        r = requests.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": "json", "keep_alive": "60m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.2, "seed": seed, "num_ctx": 6144, "num_predict": 700}}, timeout=900)
        r.raise_for_status()
        d = r.json()
        return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)


class AnthropicLLM(LLM):
    price_in, price_out = 2.0, 10.0          # claude-sonnet-5-5, $ per 1M tokens

    def __init__(self, model: str = None):
        super().__init__()
        import anthropic
        e = _env()
        self.model = model or e.get("HOSTED_MODEL") or "claude-sonnet-5-5"
        self.name = f"hosted:{self.model}"
        self.client = anthropic.Anthropic(api_key=e.get("ANTHROPIC_API_KEY") or None, max_retries=6)

    def _call(self, system, user, seed):
        # Sonnet 5.5 rejects non-default temperature; repeats rely on natural sampling (seed only keys the cache).
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=4000, system=system,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            thinking={"type": "between_tools"}, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": user}])
        if r.stop_reason == "refusal":
            return "{}", r.usage.input_tokens, r.usage.output_tokens
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens


def get_llm(kind: str) -> LLM:
    return OllamaLLM() if kind == "local" else AnthropicLLM()
