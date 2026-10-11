"""The AI services Ask and Review can use, how each is set up, and how a
request is sent to one.

Two of them run on this computer (Ollama, which the app installs, and LM
Studio, if the firm has it); the others are online services that receive
the passages a request includes. Whether a provider is "on this computer"
is worked out from its address, never from a setting: a local provider
pointed at another machine counts as sending data off this machine, so the
confirmation step applies to it too.

Claude is called through Anthropic's SDK; the others share the
OpenAI-style chat API and are called over plain HTTPS."""

import ipaddress
import json
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .. import audit
from ..settings import Settings
from . import secrets


class ProviderError(Exception):
    """A request that didn't work, with a message for the person."""


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    api: str  # "anthropic" or "openai"
    base_url: str
    model: str
    needs_key: bool
    url_editable: bool
    maker: str  # who receives the data, when it leaves this machine
    about: str


def catalog(cfg: Settings) -> dict[str, Kind]:
    return {k.id: k for k in (
        Kind("claude", "Claude", "anthropic", "https://api.anthropic.com", "claude-opus-5-5", True, False, "Anthropic",
             "Anthropic's Claude models. Needs an API key from console.anthropic.com."),
        Kind("openai", "OpenAI", "openai", "https://api.openai.com/v1", "gpt-5", True, False, "OpenAI",
             "OpenAI's GPT models. Needs an API key from platform.openai.com."),
        Kind("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "deepseek-chat", True, False, "DeepSeek",
             "DeepSeek's models. Needs an API key from platform.deepseek.com."),
        Kind("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", "openrouter/auto", True, False, "OpenRouter",
             "One key for many companies' models. Requests pass through OpenRouter to the model's maker."),
        # The app's own model service; its address is fixed so it can only be the local one.
        Kind("ollama", "Ollama", "openai", cfg.ollama_url.rstrip("/") + "/v1", "", False, False, "",
             "Models that run on this computer, in the Local AI models service. Nothing leaves this machine."),
        Kind("lmstudio", "LM Studio", "openai", "http://127.0.0.1:1234/v1", "", False, True, "",
             "Models served by LM Studio on this computer. Turn on its local server first."),
    )}


# Local chat models the Admin can download into Ollama from Settings.
LOCAL_MODELS = {
    "qwen3:8b": "Qwen3 8B, about 5.2 GB. Good all-rounder; needs 16 GB of memory.",
    "llama3.1:8b": "Llama 3.1 8B, about 4.9 GB. Needs 16 GB of memory.",
    "gemma3:12b": "Gemma 3 12B, about 8.1 GB. Better answers; needs 24 GB of memory.",
}

# Claude models that may hand a declined request to another Claude model
# (server-side fallback). The request still goes only to Anthropic.
_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


@dataclass
class Provider:
    kind: Kind
    enabled: bool
    base_url: str
    model: str
    key: bytes | None
    key_hint: str
    tested_at: str | None
    test_ok: bool | None

    @property
    def id(self) -> str:
        return self.kind.id

    @property
    def external(self) -> bool:
        return not is_loopback(self.base_url)

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.model) and (self.key is not None or not self.kind.needs_key)

    @property
    def name(self) -> str:
        """How the provider is named in the confirmation and audit log."""
        if self.kind.id == "lmstudio" and self.external:
            return f"LM Studio on {urlsplit(self.base_url).hostname}"
        return self.kind.label

    @property
    def destination(self) -> str:
        """Where the data goes: "Anthropic", "LM Studio on 192.168.1.20"."""
        return (self.kind.maker or self.name) if self.external else "this computer"

    def budget(self) -> int:
        """How much case text, in characters, one request may carry. Local
        models have small context windows; online ones take far more."""
        return 400_000 if self.kind.id in ("claude", "openai", "openrouter") else 120_000 if self.kind.id == "deepseek" else 24_000

    def view(self) -> dict:
        """What the Settings screen sees: never the key itself."""
        return {
            "id": self.id, "label": self.kind.label, "about": self.kind.about, "name": self.name, "destination": self.destination,
            "enabled": self.enabled, "base_url": self.base_url, "url_editable": self.kind.url_editable,
            "model": self.model, "default_model": self.kind.model, "needs_key": self.kind.needs_key,
            "has_key": self.key is not None, "key_hint": self.key_hint, "external": self.external,
            "ready": self.ready, "tested_at": self.tested_at, "test_ok": self.test_ok,
        }


def is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_cloud_model(model: str) -> bool:
    """Ollama's cloud models run on Ollama's servers, not here."""
    m = model.lower()
    return m.endswith("-cloud") or ":cloud" in m or "-cloud:" in m


def load_all(db: sqlite3.Connection, cfg: Settings) -> list[Provider]:
    rows = {r["id"]: r for r in db.execute("SELECT * FROM ai_providers")}
    out = []
    for kind in catalog(cfg).values():
        r = rows.get(kind.id)
        base = r["base_url"] if r is not None and kind.url_editable and r["base_url"] else kind.base_url
        out.append(Provider(
            kind=kind,
            enabled=bool(r["enabled"]) if r is not None else False,
            base_url=base,
            model=(r["model"] if r is not None and r["model"] else kind.model),
            key=r["api_key"] if r is not None else None,
            key_hint=r["key_hint"] if r is not None else "",
            tested_at=r["tested_at"] if r is not None else None,
            test_ok=(None if r is None or r["test_ok"] is None else bool(r["test_ok"])),
        ))
    return out


def load(db: sqlite3.Connection, cfg: Settings, provider_id: str) -> Provider | None:
    return next((p for p in load_all(db, cfg) if p.id == provider_id), None)


def defaults(db: sqlite3.Connection) -> dict[str, str]:
    values = {r["name"]: r["value"] for r in db.execute("SELECT name, value FROM ai_settings")}
    return {"ask": values.get("ask_provider", ""), "review": values.get("review_provider", "")}


def save(db: sqlite3.Connection, cfg: Settings, p: Provider, user: dict) -> None:
    db.execute(
        """INSERT INTO ai_providers (id, enabled, base_url, model, api_key, key_hint, tested_at, test_ok, updated_at, updated_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET enabled = excluded.enabled, base_url = excluded.base_url, model = excluded.model,
               api_key = excluded.api_key, key_hint = excluded.key_hint, tested_at = excluded.tested_at, test_ok = excluded.test_ok,
               updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
        (p.id, int(p.enabled), p.base_url if p.kind.url_editable else "", p.model, p.key, p.key_hint,
         p.tested_at, None if p.test_ok is None else int(p.test_ok), audit.now(), user["id"]),
    )


def check_url(url: str, needs_key: bool) -> str:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ProviderError("Enter the server's address, like http://127.0.0.1:1234/v1.")
    if parts.scheme == "http" and needs_key and not is_loopback(url):
        raise ProviderError("Use an https:// address for a server on another computer, so the API key isn't sent unencrypted.")
    return url


def api_key(p: Provider, cfg: Settings) -> str | None:
    if p.key is None:
        return None
    try:
        return secrets.unprotect(p.key, cfg.run_dir)
    except secrets.SecretError:
        raise ProviderError(f"The saved {p.kind.label} API key can't be read on this computer any more. "
                            "The Admin needs to enter it again in Settings.") from None


# --- talking to a provider --------------------------------------------------------------


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cut_short: bool


def _timeout(p: Provider) -> float:
    # Local models on an ordinary computer can take minutes over a long review.
    return 900.0 if not p.external else 600.0


def complete(p: Provider, key: str | None, system: str, prompt: str, max_tokens: int) -> Reply:
    if p.kind.api == "anthropic":
        return _claude(p, key, system, prompt, max_tokens)
    return _openai(p, key, system, prompt, max_tokens)


def _claude(p: Provider, key: str | None, system: str, prompt: str, max_tokens: int) -> Reply:
    import anthropic

    client = anthropic.Anthropic(api_key=key, base_url=p.base_url, timeout=_timeout(p), max_retries=2)
    args = {"model": p.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": prompt}]}
    try:
        # Streamed, so a long answer never hits a request time limit.
        if p.model in _FALLBACK_MODELS:
            with client.beta.messages.stream(**args, betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
                msg = stream.get_final_message()
        else:
            with client.messages.stream(**args) as stream:
                msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ProviderError("Claude refused the API key. The Admin can check it in Settings.") from None
    except anthropic.PermissionDeniedError:
        raise ProviderError("This Claude API key isn't allowed to use that model. The Admin can check it in Settings.") from None
    except anthropic.NotFoundError:
        raise ProviderError(f"Claude doesn't recognise the model “{p.model}”. The Admin can choose another in Settings.") from None
    except anthropic.RateLimitError:
        raise ProviderError("Claude is limiting how many requests this key can make right now. Wait a minute and try again.") from None
    except anthropic.BadRequestError as exc:
        if "too long" in str(exc).lower():
            raise ProviderError("That was more text than the model accepts in one go. Choose a smaller scope and try again.") from None
        raise ProviderError("Claude couldn't accept this request. Try again; if it keeps happening, the Admin can test the connection in Settings.") from None
    except anthropic.APIStatusError:
        raise ProviderError("Claude's service is having problems. Try again in a few minutes.") from None
    except anthropic.APIConnectionError:
        raise ProviderError("Couldn't reach Claude. Check this computer's internet connection and try again.") from None
    if msg.stop_reason == "refusal":
        raise ProviderError("Claude declined to answer this request. Try rewording it or choosing other documents.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return Reply(text, msg.model, msg.usage.input_tokens, msg.usage.output_tokens, msg.stop_reason == "max_tokens")


def _openai(p: Provider, key: str | None, system: str, prompt: str, max_tokens: int) -> Reply:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if p.id == "openrouter":
        headers["X-Title"] = "Case File Manager"
    body: dict = {"model": p.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "stream": False}
    # OpenAI's newer models only accept max_completion_tokens.
    body["max_completion_tokens" if p.id == "openai" else "max_tokens"] = max_tokens
    r = _post(p, "/chat/completions", headers, body)
    try:
        data = r.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError(f"{p.name} sent back an answer this app couldn't read. Try again.") from None
    usage = data.get("usage") or {}
    return Reply(text, data.get("model") or p.model, usage.get("prompt_tokens"), usage.get("completion_tokens"), choice.get("finish_reason") == "length")


def _post(p: Provider, path: str, headers: dict, body: dict) -> httpx.Response:
    try:
        r = httpx.post(p.base_url + path, headers=headers, content=json.dumps(body), timeout=httpx.Timeout(_timeout(p), connect=10.0))
    except httpx.ConnectError:
        raise ProviderError(_unreachable(p)) from None
    except httpx.TimeoutException:
        raise ProviderError(f"{p.name} took too long to answer. Try a smaller scope, or try again later.") from None
    except httpx.HTTPError:
        raise ProviderError(_unreachable(p)) from None
    _raise_for(p, r)
    return r


def _unreachable(p: Provider) -> str:
    if p.id == "ollama":
        return "The Local AI models service isn't answering. The Admin can start it from the Control Center."
    if p.id == "lmstudio":
        return "LM Studio isn't answering. Open LM Studio and start its local server, then try again."
    return f"Couldn't reach {p.name}. Check this computer's internet connection and try again."


def _raise_for(p: Provider, r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    detail = ""
    try:
        err = r.json().get("error")
        detail = (err.get("message") if isinstance(err, dict) else str(err or "")).lower()
    except (ValueError, AttributeError):
        pass
    if r.status_code in (401, 403):
        raise ProviderError(f"{p.name} refused the API key. The Admin can check it in Settings.")
    if r.status_code == 404 or "model" in detail and ("not found" in detail or "does not exist" in detail):
        raise ProviderError(f"{p.name} doesn't have the model “{p.model}”. The Admin can choose another in Settings.")
    if r.status_code == 429:
        raise ProviderError(f"{p.name} is limiting requests right now, or the account is out of credit. Wait a minute and try again.")
    if r.status_code in (400, 413) and ("context" in detail or "too long" in detail or "maximum" in detail or r.status_code == 413):
        raise ProviderError("That was more text than the model accepts in one go. Choose a smaller scope and try again.")
    if r.status_code >= 500:
        raise ProviderError(f"{p.name} is having problems. Try again in a few minutes.")
    raise ProviderError(f"{p.name} couldn't accept this request. The Admin can test the connection in Settings.")


def test(p: Provider, key: str | None) -> dict:
    """Check the address, key and model without sending any case text.
    Returns the models the service offers, for the Settings screen."""
    if p.kind.needs_key and not key:
        raise ProviderError("Enter the API key first.")
    if p.kind.api == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=key, base_url=p.base_url, timeout=30.0, max_retries=1)
        try:
            models = [m.id for m in client.models.list(limit=100)]
        except anthropic.AuthenticationError:
            raise ProviderError("Claude refused the API key. Check it was copied in full.") from None
        except anthropic.APIConnectionError:
            raise ProviderError("Couldn't reach Claude. Check this computer's internet connection.") from None
        except anthropic.APIStatusError:
            raise ProviderError("Claude's service answered with an error. Try again in a few minutes.") from None
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            r = httpx.get(p.base_url + "/models", headers=headers, timeout=30.0)
        except httpx.HTTPError:
            raise ProviderError(_unreachable(p)) from None
        _raise_for(p, r)
        try:
            models = sorted(m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m)
        except (ValueError, AttributeError, TypeError):
            raise ProviderError(f"{p.name} answered, but not like an AI service. Check the address.") from None
    if p.id == "ollama":
        models = [m for m in models if not is_cloud_model(m) and not m.startswith("bge-")]
    if not p.model:
        message = "Connected. Choose a model." if models else "Connected, but no chat model is available yet."
    elif models and p.model not in models:
        message = f"Connected, but “{p.model}” isn't one of the models this service offers. Choose one from the list."
    else:
        message = f"Connected. {p.model} is ready to use."
    ok = bool(p.model) and (not models or p.model in models)
    return {"ok": ok, "message": message, "models": models[:200]}
