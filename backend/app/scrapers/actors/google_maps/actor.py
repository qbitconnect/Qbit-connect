"""GoogleMapsActor — business listing extraction via pluggable provider.

The actor contains NO scraping logic against Google and no evasion of any
platform control (brief §28). It consumes a MapsProvider (provider.py): an
operator-configured compliant data endpoint, or the mock provider for tests.
Without a configured provider the actor is DEGRADED and refuses to run with a
clear configuration error (§49) — it never fakes success (§55).

Checkpoint cursor = provider page_token + records yielded so far, so pause /
crash resume resumes pagination (§18).
"""

from __future__ import annotations

from app.scrapers.actors.google_maps.provider import build_maps_provider
from app.scrapers.actors.google_maps.schemas import OUTPUT_FIELDS, GoogleMapsInput
from app.scrapers.core.base import ActorCategory, ActorHealth, ActorStatus, ScraperActor
from app.scrapers.core.exceptions import ScraperConfigurationError


class GoogleMapsActor(ScraperActor):
    id = "google-maps"
    name = "Google Maps"
    version = "1.0.0"
    description = (
        "Business listing extraction (name, category, phone, website, "
        "address, rating) through a configurable compliant data provider. "
        "No CAPTCHA bypass, no anti-bot evasion — by design."
    )
    category = ActorCategory.BUSINESS_LEADS
    author = "QBIT"
    capabilities = (
        "business listing fields",
        "provider-based (configurable)",
        "pagination checkpoints",
        "compliant access only",
    )
    supports_pause = True
    required_credentials = ("QBIT_MAPS_PROVIDER_API_KEY",)
    rate_limit_per_minute = 120
    concurrency_limit = 5
    input_schema = GoogleMapsInput
    output_fields = OUTPUT_FIELDS

    def __init__(self, settings=None) -> None:
        self._settings = settings

    def validate_policy(self, model) -> dict[str, str]:
        errors: dict[str, str] = {}
        inp: GoogleMapsInput = model  # type: ignore
        q = (inp.query or "").strip()
        cat = (inp.category or "").strip()
        if not q and not cat:
            errors["query"] = "Either query or category must be specified"
        if inp.radius_meters is not None and not (100 <= inp.radius_meters <= 100000):
            errors["radius_meters"] = "Radius must be between 100 and 100,000 meters"
        for field_name in ("query", "category", "city", "state", "country", "region"):
            val = getattr(inp, field_name, None)
            if val and ("<script" in val.lower() or "</script" in val.lower()):
                errors[field_name] = f"Invalid script markup detected in {field_name}"
        return errors

    async def health_check(self) -> ActorHealth:
        if self._settings is None:
            return ActorHealth(status=ActorStatus.READY, detail="No settings wired (tests)")
        try:
            provider = build_maps_provider(self._settings)
        except Exception as exc:  # noqa: BLE001 — configuration problems surface
            return ActorHealth(status=ActorStatus.FAILED, detail=str(exc))
        if provider is None:
            return ActorHealth(
                status=ActorStatus.DEGRADED,
                detail="CONFIGURATION REQUIRED: No maps provider configured (QBIT_MAPS_PROVIDER=none); "
                       "configure a compliant provider (outscraper / http) to enable this actor.",
                dependencies={"maps_provider": "missing"},
            )
        return ActorHealth(
            status=ActorStatus.READY,
            detail=f"provider={provider.name}",
            dependencies={"maps_provider": provider.name},
        )

    async def verify_connection(self, http=None) -> dict:
        """Lightweight live verification of provider credentials and connectivity."""
        if self._settings is None:
            return {
                "connected": False,
                "provider": "none",
                "status": "CONFIGURATION REQUIRED",
                "detail": "No settings wired",
                "metadata": {"configured": False},
            }
        try:
            provider = build_maps_provider(self._settings)
        except Exception as exc:
            return {
                "connected": False,
                "provider": "error",
                "status": "CONFIGURATION ERROR",
                "detail": str(exc),
                "metadata": {"configured": False, "error": str(exc)},
            }
        if provider is None:
            return {
                "connected": False,
                "provider": "none",
                "status": "CONFIGURATION REQUIRED",
                "detail": "No maps provider configured. Set QBIT_MAPS_PROVIDER and QBIT_MAPS_PROVIDER_API_KEY in .env",
                "metadata": {"configured": False},
            }

        # If http client not provided, use an ephemeral client for verification
        if http is None:
            from app.scrapers.core.http import HttpPolicy, PolicyHttpClient
            from app.scrapers.core.netguard import UrlPolicy
            policy = HttpPolicy(request_timeout=8.0, max_retries=1, respect_robots=False)
            url_policy = UrlPolicy(allow_private_targets=True)
            async with PolicyHttpClient(policy, url_policy) as client:
                return await provider.verify_connection(client)

        return await provider.verify_connection(http)

    async def run(self, ctx):
        inp = GoogleMapsInput.model_validate(ctx.input)
        if self._settings is None:
            raise ScraperConfigurationError(
                "google-maps actor requires settings (provider configuration)"
            )
        provider = build_maps_provider(self._settings)
        if provider is None:
            raise ScraperConfigurationError(
                "No maps provider configured. Set QBIT_MAPS_PROVIDER=outscraper "
                "(with QBIT_MAPS_PROVIDER_API_KEY) or QBIT_MAPS_PROVIDER=http "
                "(with QBIT_MAPS_PROVIDER_URL). Direct scraping/evasion of Google "
                "is not supported by design."
            )

        page_token: str | None = None
        if ctx.checkpoint and ctx.checkpoint.data.get("page_token"):
            page_token = ctx.checkpoint.data["page_token"]
        elif ctx.checkpoint:
            ctx.checkpoint_cursor({"page_token": None})

        yielded = 0
        while yielded < inp.max_results:
            await ctx.check_stopped()
            ctx.check_deadline()
            ctx.progress.set_stage(f"fetching provider page (token={page_token})")
            raw_items, next_token = await provider.search(
                query=inp.query,
                category=inp.category,
                city=inp.city,
                state=inp.state,
                country=inp.country,
                region=inp.region,
                radius_meters=inp.radius_meters,
                drop_duplicates=inp.drop_duplicates,
                language=inp.language,
                page_token=page_token,
                max_results=inp.max_results - yielded,
                http=ctx.http,
            )
            if not raw_items:
                break
            for raw in raw_items:
                await ctx.check_stopped()
                raw["source"] = self.id
                if not raw.get("source_url"):
                    raw["source_url"] = (
                        f"https://www.google.com/maps/search/{inp.query.replace(' ', '+')}"
                    )
                yield raw
                yielded += 1
                if yielded >= inp.max_results:
                    break
            await ctx.save_checkpoint({"page_token": next_token, "yielded": yielded})
            ctx.progress.set_stage(f"provider pages done ({yielded} records)")
            if next_token is None:
                break
            page_token = next_token

        await ctx.save_checkpoint({"page_token": None, "yielded": yielded}, force=True)

    async def cleanup(self, ctx) -> None:
        await ctx.close()
