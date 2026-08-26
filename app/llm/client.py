"""Provider-agnostic LLM layer.

Two backends behind one interface: OpenRouter (OpenAI-compatible) and the
Anthropic SDK. Which one runs is decided by LLM_PROVIDER in .env, so switching
costs one line and no code changes. Both paths return validated pydantic models
and log token spend into the costs table.
"""
import json
import logging
import re
import time
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app import config
from app.db.base import x
from app.db.repo import now

log = logging.getLogger("painbot.llm")

T = TypeVar("T", bound=BaseModel)

SCREEN_MODEL = config.MODEL_SCREEN
IDEATE_MODEL = config.MODEL_IDEATE
WRITE_MODEL = config.MODEL_WRITE

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENAI_LIKE = ("openrouter", "kie")

# USD per million tokens (input, output). OpenRouter prices are fetched live on
# first use; these cover the Anthropic path and act as a fallback.
STATIC_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
}
_or_pricing: Optional[dict] = None
_clients: dict = {}


def _openrouter_pricing() -> dict:
    """One call to /models gives real prices, so they never go stale here."""
    global _or_pricing
    if _or_pricing is not None:
        return _or_pricing
    _or_pricing = {}
    try:
        import httpx

        response = httpx.get(OPENROUTER_BASE + "/models", timeout=20)
        response.raise_for_status()
        for model in response.json().get("data", []):
            price = model.get("pricing") or {}
            _or_pricing[model["id"]] = (
                float(price.get("prompt") or 0) * 1_000_000,
                float(price.get("completion") or 0) * 1_000_000,
            )
    except Exception as exc:
        log.warning("could not fetch openrouter pricing: %s", exc)
    return _or_pricing


# kie charges in credits at roughly 40% of the vendors' list price.
KIE_PRICING = {
    "gemini-3-7-flash-openai": (0.15, 0.75),
    "claude-haiku-4-5": (0.40, 2.00),
    "claude-sonnet-5": (1.20, 6.00),
    "claude-opus-4-8": (2.00, 10.00),
}


def kie_credits() -> Optional[float]:
    """Their own balance is the honest cost readout; no price table can drift."""
    try:
        import httpx

        r = httpx.get(
            "https://api.kie.ai/api/v1/chat/credit",
            headers={"Authorization": "Bearer " + config.KIE_API_KEY},
            timeout=20,
        )
        return float(r.json().get("data")) if r.status_code == 200 else None
    except Exception:
        return None


def _log_openai_cost(model: str, response: Any, job_id: Optional[int]) -> None:
    """kie reports credits_consumed per call, which beats any price table."""
    usage = getattr(response, "usage", None)
    tok_in = getattr(usage, "prompt_tokens", 0) or 0
    tok_out = getattr(usage, "completion_tokens", 0) or 0
    credits = getattr(response, "credits_consumed", None)
    if credits is None and hasattr(response, "model_extra"):
        credits = (response.model_extra or {}).get("credits_consumed")
    if config.LLM_PROVIDER == "kie" and credits is not None:
        usd = float(credits) * 0.005  # 400 credits = $2.00 per their pricing
        x(
            "INSERT INTO costs(job_id, provider, model, tok_in, tok_out, usd, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            job_id, "kie", model, tok_in, tok_out, round(usd, 6), now(),
        )
        log.info("%s: %s in / %s out = %.3f кредитов", model, tok_in, tok_out, float(credits))
        return
    _log_cost(model, tok_in, tok_out, job_id)


def _price_of(model: str):
    if config.LLM_PROVIDER == "kie":
        return KIE_PRICING.get(model, (0.0, 0.0))
    if config.LLM_PROVIDER == "openrouter":
        return _openrouter_pricing().get(model, (0.0, 0.0))
    return STATIC_PRICING.get(model, (0.0, 0.0))


def _log_cost(model: str, tok_in: int, tok_out: int, job_id: Optional[int]) -> float:
    price_in, price_out = _price_of(model)
    usd = tok_in / 1_000_000 * price_in + tok_out / 1_000_000 * price_out
    x(
        "INSERT INTO costs(job_id, provider, model, tok_in, tok_out, usd, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        job_id,
        config.LLM_PROVIDER,
        model,
        tok_in,
        tok_out,
        round(usd, 6),
        now(),
    )
    log.info("%s: %s in / %s out = $%.4f", model, tok_in, tok_out, usd)
    return usd


def client(model: str = "") -> Any:
    """kie puts the model in the base URL, so its clients are cached per model."""
    cache_key = model if config.LLM_PROVIDER == "kie" else config.LLM_PROVIDER
    if cache_key in _clients:
        return _clients[cache_key]

    if config.LLM_PROVIDER == "kie":
        if not config.KIE_API_KEY:
            raise RuntimeError("KIE_API_KEY пуст — заполни painbot/.env")
        from openai import OpenAI

        made = OpenAI(
            base_url=config.KIE_BASE.format(model=model),
            api_key=config.KIE_API_KEY,
        )
    elif config.LLM_PROVIDER == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY пуст — заполни painbot/.env")
        from openai import OpenAI

        made = OpenAI(
            base_url=OPENROUTER_BASE,
            api_key=config.OPENROUTER_API_KEY,
            default_headers={"X-Title": "painbot"},
        )
    else:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY пуст — заполни painbot/.env")
        import anthropic

        made = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    _clients[cache_key] = made
    return made


def _strictify(node: Any) -> Any:
    """Structured output wants every object closed and every field required."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props)
        for key in list(node):
            if key == "$ref":
                continue
            node[key] = _strictify(node[key])
        node.pop("default", None)
    elif isinstance(node, list):
        return [_strictify(item) for item in node]
    return node


def _inline_refs(node: Any, defs: dict) -> Any:
    """Google's schema validator has no $ref, so nested models are inlined.

    Pydantic emits every nested model into $defs and points at it; sending that
    to kie's Gemini proxy comes back as a 500.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1], {})
            merged = dict(target)
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = value
            return _inline_refs(merged, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _json_schema(schema: Type[BaseModel]) -> dict:
    raw = json.loads(json.dumps(schema.model_json_schema()))
    defs = raw.pop("$defs", {})
    return _strictify(_inline_refs(raw, defs))


_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def _loads(raw: str, schema: Type[T]) -> T:
    """Tolerate a fenced or chatty answer; strict mode is not always honoured."""
    text_value = (raw or "").strip()
    try:
        return schema.model_validate_json(text_value)
    except (ValidationError, ValueError):
        pass
    match = _FENCE.search(text_value)
    if match:
        return schema.model_validate_json(match.group(1).strip())
    start, end = text_value.find("{"), text_value.rfind("}")
    if start != -1 and end > start:
        return schema.model_validate_json(text_value[start : end + 1])
    raise ValueError("модель вернула не JSON: " + text_value[:200])


def _ask_openai(model: str, body: dict, tries: int = 3) -> Any:
    """kie drops an error envelope on maybe one call in twenty; retry it."""
    last = None
    for attempt in range(tries):
        response = client(model).chat.completions.create(**body)
        choices = getattr(response, "choices", None)
        # an empty body counts as a failure too: kie sometimes answers with a
        # well-formed envelope and nothing inside it
        if choices and (choices[0].message.content or "").strip():
            return response
        last = response
        log.warning("provider returned no choices, retry %s/%s", attempt + 1, tries)
        time.sleep(1.5 * (attempt + 1))
    return last


def _first_choice(response: Any) -> str:
    """kie answers 200 with an error envelope, so choices can simply be absent.

    Without this the caller died on a bare TypeError that said nothing about
    what actually went wrong.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        detail = ""
        for attr in ("model_extra", "__dict__"):
            data = getattr(response, attr, None)
            if isinstance(data, dict):
                detail = str(data.get("msg") or data.get("error") or "")[:200]
                if detail:
                    break
        raise RuntimeError("провайдер вернул ответ без choices" + (": " + detail if detail else ""))
    return choices[0].message.content or ""


def _messages(system: Optional[str], prompt: str) -> list:
    head = [{"role": "system", "content": system}] if system else []
    return head + [{"role": "user", "content": prompt}]


def parse(
    schema: Type[T],
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 16000,
    job_id: Optional[int] = None,
) -> T:
    model = model or SCREEN_MODEL

    if config.LLM_PROVIDER in OPENAI_LIKE:
        response = _ask_openai(model, dict(
            model=model,
            max_tokens=max_tokens,
            messages=_messages(system, prompt),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__.lower(),
                    "strict": True,
                    "schema": _json_schema(schema),
                },
            },
        ))
        _log_openai_cost(model, response, job_id)
        return _loads(_first_choice(response), schema)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": schema,
    }
    if system:
        kwargs["system"] = system
    response = client(model).messages.parse(**kwargs)
    _log_cost(
        model,
        getattr(response.usage, "input_tokens", 0) or 0,
        getattr(response.usage, "output_tokens", 0) or 0,
        job_id,
    )
    return response.parsed_output


def text(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 16000,
    job_id: Optional[int] = None,
) -> str:
    model = model or WRITE_MODEL

    if config.LLM_PROVIDER in OPENAI_LIKE:
        response = _ask_openai(model, dict(
            model=model, max_tokens=max_tokens, messages=_messages(system, prompt)
        ))
        _log_openai_cost(model, response, job_id)
        return _first_choice(response)

    import anthropic

    response = client(model).messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
    )
    _log_cost(
        model,
        getattr(response.usage, "input_tokens", 0) or 0,
        getattr(response.usage, "output_tokens", 0) or 0,
        job_id,
    )
    return "".join(b.text for b in response.content if b.type == "text")
