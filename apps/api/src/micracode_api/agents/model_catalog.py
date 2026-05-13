"""Catalog of provider/model pairs the API will accept at runtime.

Centralising this here keeps three concerns in one place:

- the allow-list the client picker renders,
- the allow-list the ``/v1/generate`` endpoint validates against,
- a fallback resolver used when the client does not specify a model
  (the first turn after boot, or older clients pinned to env-driven
  defaults).

API keys are intentionally NOT exposed; ``available`` is a boolean the
UI uses to disable entries whose server-side key is missing.

Ollama models are fetched dynamically from the local Ollama daemon via
``GET /api/tags``; the section is omitted entirely if Ollama is not
running or has no models installed.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings

ProviderId = str


@dataclass(frozen=True)
class _Model:
    id: str
    label: str


@dataclass(frozen=True)
class _Provider:
    id: str
    label: str
    models: tuple[_Model, ...]


# Registry for static (cloud) providers. To add a new model ID, append a
# ``_Model`` entry below; no other changes are required for it to show up
# in the UI picker.
_PROVIDERS: tuple[_Provider, ...] = (
    _Provider(
        id="openai",
        label="OpenAI",
        models=(
            _Model(id="gpt-5.4", label="GPT-5.4"),
            _Model(id="gpt-5-mini", label="GPT-5 Mini"),
            _Model(id="gpt-4.1", label="GPT-4.1"),
        ),
    ),
    _Provider(
        id="gemini",
        label="Google Gemini",
        models=(
            _Model(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
            _Model(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
            _Model(id="gemini-2.5-flash-lite", label="Gemini 2.5 Flash Lite"),
        ),
    ),
)


def _provider(pid: str) -> _Provider | None:
    for p in _PROVIDERS:
        if p.id == pid:
            return p
    return None


def _has_model(provider: _Provider, model_id: str) -> bool:
    return any(m.id == model_id for m in provider.models)


def _provider_available(settings: Settings, pid: str) -> bool:
    if pid == "openai":
        return bool(settings.openai_api_key)
    if pid == "gemini":
        return bool(settings.google_api_key)
    return False


async def _fetch_ollama_models(base_url: str) -> list[str]:
    """Return installed model names from the local Ollama daemon, or [] on any error."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


async def list_catalog(settings: Settings) -> dict:
    """Serialise the registry for the public ``GET /v1/models`` endpoint."""
    providers = [
        {
            "id": p.id,
            "label": p.label,
            "available": _provider_available(settings, p.id),
            "models": [{"id": m.id, "label": m.label} for m in p.models],
        }
        for p in _PROVIDERS
    ]

    ollama_models = await _fetch_ollama_models(settings.ollama_base_url)
    if ollama_models:
        providers.append(
            {
                "id": "ollama",
                "label": "Ollama (local)",
                "available": True,
                "models": [{"id": name, "label": name} for name in ollama_models],
            }
        )

    default = _default_selection(settings, ollama_models)

    return {
        "providers": providers,
        "default": {"provider": default[0], "model": default[1]},
        "locked": bool(settings.lock_model_selection),
    }


def _default_selection(
    settings: Settings, ollama_models: list[str] | None = None
) -> tuple[str, str]:
    """Pick a sensible default for clients that haven't chosen yet.

    Prefers ``settings.llm_provider`` + ``settings.active_model`` when valid;
    otherwise falls back to the first available provider, then the first
    provider overall.
    """
    env_provider = settings.llm_provider
    env_model = settings.active_model

    if env_provider == "ollama":
        if env_model:
            return ("ollama", env_model)
        if ollama_models:
            return ("ollama", ollama_models[0])
    else:
        env = _provider(env_provider)
        if env is not None and env_model and _has_model(env, env_model):
            return (env_provider, env_model)

    for p in _PROVIDERS:
        if _provider_available(settings, p.id) and p.models:
            return (p.id, p.models[0].id)

    if ollama_models:
        return ("ollama", ollama_models[0])

    first = _PROVIDERS[0]
    return (first.id, first.models[0].id)


def resolve(
    provider: str | None,
    model: str | None,
    settings: Settings,
) -> tuple[str, str]:
    """Validate a requested ``(provider, model)`` pair, filling in defaults.

    Rules:

    - Missing provider+model -> use :func:`_default_selection`.
    - Partial input -> reject with ``ValueError``; requiring both together
      keeps the contract simple (the UI always sends both or neither).
    - For ``ollama``: any non-empty model name is accepted (pass-through);
      validation happens at generation time via the Ollama daemon.
    - Unknown provider or model id (not in the static registry) -> ``ValueError``.
    - Provider whose API key is not configured on the server ->
      ``ValueError`` so the caller sees a clean error frame instead of a
      provider-SDK auth error.
    """
    if settings.lock_model_selection:
        return _default_selection(settings)

    if provider is None and model is None:
        return _default_selection(settings)

    if provider is None or model is None:
        raise ValueError(
            "Both 'provider' and 'model' must be supplied together; got "
            f"provider={provider!r} model={model!r}."
        )

    if provider == "ollama":
        if not model:
            raise ValueError("model must be non-empty for provider 'ollama'.")
        return ("ollama", model)

    p = _provider(provider)
    if p is None:
        known = ", ".join(pp.id for pp in _PROVIDERS) + ", ollama"
        raise ValueError(
            f"Unknown provider {provider!r}; supported providers: {known}."
        )

    if not _has_model(p, model):
        allowed = ", ".join(m.id for m in p.models)
        raise ValueError(
            f"Unknown model {model!r} for provider {provider!r}; "
            f"supported models: {allowed}."
        )

    if not _provider_available(settings, p.id):
        env_var = "OPENAI_API_KEY" if p.id == "openai" else "GOOGLE_API_KEY"
        raise ValueError(
            f"Provider {p.id!r} is selected but {env_var} is not configured "
            "on the server."
        )

    return (p.id, model)
