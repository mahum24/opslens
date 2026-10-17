"""The language models the assistant can use.

Two providers on purpose: Gemini (an external API) and Ollama (a model
running on our own machine or data centre). Which one to use is a data
governance decision, so it is a setting, and the assistant masks personal
data whenever the model is external.
"""
import json
import re
import time

import requests

from opslens import settings


class LLMError(Exception):
    pass


def parse_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise LLMError(f"Model did not return JSON: {text[:200]}")


class GeminiLLM:
    external = True

    def __init__(self, api_key=None, model=None, fallback_model="gemini-flash-lite-latest"):
        from google import genai
        key = api_key or settings.GEMINI_API_KEY
        if not key:
            raise LLMError("GEMINI_API_KEY is not set (add it to .env, or switch LLM_PROVIDER to ollama)")
        self.client = genai.Client(api_key=key)
        self.models = [model or settings.GEMINI_MODEL, fallback_model]
        self.name = f"Gemini ({self.models[0]})"

    def json(self, system: str, prompt: str) -> dict:
        from google.genai import types
        cfg = types.GenerateContentConfig(system_instruction=system, temperature=0,
                                          response_mime_type="application/json")
        last = None
        # The free tier is often busy (429/503): retry with a short back-off, then try the lite model.
        for model in self.models:
            for wait in (0, 2, 5):
                time.sleep(wait)
                try:
                    resp = self.client.models.generate_content(model=model, contents=prompt, config=cfg)
                    return parse_json(resp.text or "")
                except LLMError:
                    raise
                except Exception as e:
                    last = e
                    if not any(code in str(e) for code in ("429", "500", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                        break
        raise LLMError(f"Gemini is not available right now ({str(last)[:120]})")


class OllamaLLM:
    external = False

    def __init__(self, url=None, model=None):
        self.url = (url or settings.OLLAMA_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.name = f"Ollama ({self.model}, self-hosted)"

    def json(self, system: str, prompt: str) -> dict:
        try:
            r = requests.post(f"{self.url}/api/chat", timeout=180, json={
                "model": self.model, "stream": False, "format": "json", "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(f"Could not reach Ollama at {self.url}: {e}")
        return parse_json(r.json()["message"]["content"])


def get_llm(provider: str | None = None):
    provider = (provider or settings.LLM_PROVIDER).lower()
    if provider == "gemini":
        return GeminiLLM()
    if provider == "ollama":
        return OllamaLLM()
    raise LLMError(f"Unknown LLM provider '{provider}'")
