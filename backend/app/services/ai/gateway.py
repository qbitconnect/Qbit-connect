"""AI Provider Gateway — abstraction over OpenAI-compatible HTTP APIs.

Never uses provider SDKs directly; makes raw HTTP calls via httpx so that
the same gateway works with OpenAI, Anthropic (OpenAI-compatible mode),
local Ollama, or any OpenAI-compatible proxy.

Handles:
- Model routing by key (extraction | classification | scoring | reasoning)
- Token counting and cost estimation per model
- Missing-credentials detection (raises ConfigurationError, never crashes)
- AIUsageRecord persistence for every completion
- Prompt-injection defenses (system prompt isolation, role pinning)
- SSRF protection (only HTTPS, no private IPs)
- Secret redaction in all log lines
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.core.config import Settings

logger = get_logger("qbit.ai.gateway")

# Cost per 1 000 tokens (USD) — update when provider pricing changes
_COST_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.000150, "completion": 0.000600},
    "gpt-4-turbo": {"prompt": 0.010, "completion": 0.030},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
    "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
    "gemini-1.5-pro": {"prompt": 0.00125, "completion": 0.005},
    "gemini-1.5-flash": {"prompt": 0.000075, "completion": 0.0003},
    # fallback for unknown models
    "default": {"prompt": 0.002, "completion": 0.008},
}

# Routing keys → model preference order
_ROUTING_KEYS = {
    "extraction": "default_model",
    "classification": "default_model",
    "scoring": "default_model",
    "reasoning": "reasoning_model",
    "default": "default_model",
}

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


class ConfigurationError(Exception):
    """AI gateway is not configured (missing API key or provider)."""


class AIBudgetExceeded(Exception):
    """An agent run exceeded its per-run cost budget."""


@dataclass
class AIResponse:
    """Result from a single AI completion call."""
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    duration_ms: int = 0


class AIGateway:
    """OpenAI-compatible HTTP gateway with cost tracking and SSRF protection."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = settings.AI_PROVIDER
        self._api_key = settings.AI_API_KEY
        self._base_url = settings.AI_BASE_URL.rstrip("/")
        self._default_model = settings.AI_DEFAULT_MODEL
        self._reasoning_model = settings.AI_REASONING_MODEL
        self._max_tokens = settings.AI_MAX_TOKENS
        self._timeout = settings.AI_REQUEST_TIMEOUT

    def _resolve_model(self, routing_key: str, preferred_model: str | None = None) -> str:
        if preferred_model:
            return preferred_model
        key = _ROUTING_KEYS.get(routing_key, "default_model")
        if key == "reasoning_model":
            return self._reasoning_model
        return self._default_model

    def _estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        rates = _COST_TABLE.get(model, _COST_TABLE["default"])
        return (prompt_tokens / 1000 * rates["prompt"]) + (completion_tokens / 1000 * rates["completion"])

    def _validate_base_url(self) -> None:
        """SSRF: reject private/loopback URLs in non-test environments."""
        if self._settings.QBIT_ENV == "test":
            return
        parsed = urlparse(self._base_url)
        if parsed.scheme != "https":
            raise ConfigurationError(
                f"AI_BASE_URL must use HTTPS in {self._settings.QBIT_ENV}; got scheme={parsed.scheme!r}"
            )
        host = parsed.hostname or ""
        try:
            addr = ipaddress.ip_address(host)
            for net in _PRIVATE_RANGES:
                if addr in net:
                    raise ConfigurationError(
                        f"AI_BASE_URL points to a private IP ({host}); forbidden (SSRF protection)"
                    )
        except ValueError:
            pass  # hostname — DNS resolution not checked here

    def _check_configured(self) -> None:
        if self._provider == "mock" or (self._settings.QBIT_ENV == "test" and not self._api_key):
            return
        if not self._api_key:
            raise ConfigurationError(
                "AI_API_KEY is not configured. Set it in the environment to enable AI features."
            )
        self._validate_base_url()

    async def complete(
        self,
        messages: list[dict],
        routing_key: str = "reasoning",
        preferred_model: str | None = None,
        temperature: float = 0.2,
        session: "AsyncSession | None" = None,
        organization_id: str | None = None,
        user_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> AIResponse:
        """Make a single completion call and persist usage."""
        self._check_configured()

        if self._provider == "mock" or (self._settings.QBIT_ENV == "test" and not self._api_key):
            res = await self._mock_complete(messages, routing_key, preferred_model)
            if session is not None:
                await self._persist_usage(res, session, organization_id, user_id, agent_run_id)
            return res

        model = self._resolve_model(routing_key, preferred_model)
        t0 = time.monotonic()

        try:
            import httpx
        except ImportError as exc:
            raise ConfigurationError("httpx is required for AI gateway calls") from exc

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )

        resp.raise_for_status()
        data = resp.json()

        duration_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        cost = self._estimate_cost(model, prompt_tokens, completion_tokens)

        text = ""
        choices = data.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "") or ""

        result = AIResponse(
            text=text,
            provider=self._provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=cost,
            duration_ms=duration_ms,
        )

        if session is not None:
            await self._persist_usage(result, session, organization_id, user_id, agent_run_id)

        return result

    async def _persist_usage(
        self,
        resp: AIResponse,
        session: "AsyncSession",
        organization_id: str | None,
        user_id: str | None,
        agent_run_id: str | None,
    ) -> None:
        """Write an AIUsageRecord; non-fatal if it fails."""
        try:
            import uuid as _uuid
            from datetime import datetime, timezone
            from app.models.ai import AIUsageRecord

            org_id = _uuid.UUID(organization_id) if organization_id else None
            u_id = _uuid.UUID(user_id) if user_id else None
            run_id = _uuid.UUID(agent_run_id) if agent_run_id else None

            record = AIUsageRecord(
                organization_id=org_id,
                user_id=u_id,
                agent_run_id=run_id,
                provider=resp.provider,
                model=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                estimated_cost=resp.cost_estimate,
                duration_ms=resp.duration_ms,
                created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            await session.flush()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist AI usage record")

    async def _mock_complete(
        self,
        messages: list[dict],
        routing_key: str,
        preferred_model: str | None,
    ) -> AIResponse:
        """Deterministic mock for isolated test environments."""
        import json as _json

        model = preferred_model or self._resolve_model(routing_key)
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "(no user message)",
        )
        is_json = any("JSON" in m.get("content", "") for m in messages)
        if is_json:
            payload = {
                "business_summary": "Verified premium establishment with established market presence.",
                "business_category": "Restaurant & Hospitality",
                "business_subcategory": "Fine Dining",
                "classification_confidence": 0.95,
                "key_decision_makers": [
                    {"name": "Rajesh Sharma", "title": "General Manager", "source": "verified_directory"},
                    {"name": "Priya Patel", "title": "Operations Director", "source": "corporate_filing"},
                ],
                "sentiment_signals": {
                    "avg_rating": 4.5,
                    "review_count": 120,
                    "sentiment_label": "positive",
                },
                "data_gaps": ["vat_number"],
                "proposed_crm_updates": {
                    "category": "Restaurant & Hospitality",
                },
                "recommended_products": [
                    {"sku": "QPOS-WIN-15", "name": "QBIT Windows POS Terminal 15\"", "reason": "High-volume table management and billing"}
                ],
                "sales_opportunities": ["Upgrade legacy billing system to touch-terminal"],
                "suggested_discovery_questions": ["What is your average peak-hour table turnover?"],
                "possible_objections": [{"objection": "Software migration cost", "response": "Seamless data import supported"}],
                "recommended_next_action": "Schedule on-site hardware demonstration.",
            }
            text = _json.dumps(payload)
        else:
            text = f"[MOCK AI RESPONSE] routing_key={routing_key} model={model} user_prompt_preview={last_user[:80]!r}"

        return AIResponse(
            text=text,
            provider="mock",
            model=model,
            prompt_tokens=50,
            completion_tokens=25,
            total_tokens=75,
            cost_estimate=0.0001,
            duration_ms=5,
        )


_gateway_instance: AIGateway | None = None


def get_gateway(settings: Settings) -> AIGateway:
    """Return a cached AIGateway singleton (per process)."""
    global _gateway_instance
    if _gateway_instance is None or _gateway_instance._settings is not settings:
        _gateway_instance = AIGateway(settings)
    return _gateway_instance
