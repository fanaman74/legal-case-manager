"""Vectors from the local embedding model, served by Ollama on this computer
(127.0.0.1). Nothing here ever calls the internet: the worker service is
blocked from it by the firewall, and Ollama's cloud models are switched off."""

import math

import httpx

BATCH = 16


class EmbedUnavailable(RuntimeError):
    """The local model isn't running or isn't installed yet."""


class Embedder:
    def __init__(self, url: str, model: str, timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(self._call(texts[i : i + BATCH]))
        return out

    def _call(self, texts: list[str]) -> list[list[float]]:
        try:
            r = httpx.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts, "truncate": True}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise EmbedUnavailable("The local AI model isn't running.") from exc
        if r.status_code == 404:
            raise EmbedUnavailable("The embedding model isn't installed yet.")
        if r.status_code != 200:
            raise EmbedUnavailable(f"The local AI model returned an error ({r.status_code}).")
        vectors = r.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise EmbedUnavailable("The local AI model returned an unexpected answer.")
        return [normalise(v) for v in vectors]


def normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
