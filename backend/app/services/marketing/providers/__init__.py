"""Provider registry (Phase 5 §3 + Phase 6 §1 + Phase 7 §1/§3).

    MarketingProvider registry
        +-- WhatsAppProvider        (REAL — WhatsApp Business Cloud API, whatsapp_cloud)
        +-- WhatsAppMockProvider    (MOCK / TEST ONLY — gated)
        +-- SMTPProvider            (REAL — email over SMTP TLS/STARTTLS, smtp)
        +-- GenericEmailAPIProvider (REAL — generic transactional email API, email_api)
        +-- EmailMockProvider       (MOCK / TEST ONLY — gated)
        +-- EmailProvider           (interface — generic EMAIL reporting id)
        +-- SMSProvider             (interface — later phase)
        +-- MockProvider            (MOCK / TEST ONLY — gated)

The registry NEVER auto-registers mock providers outside isolated test
environments, and never raises for unknown ids — callers get None and decide
how to fail (honest "Provider not configured" UX instead of stack traces).
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.marketing.providers.base import BaseMarketingProvider
from app.services.marketing.providers.email import (
    AmazonSESEmailProvider,
    EmailMockProvider,
    GenericEmailAPIProvider,
    SMTPProvider,
)
from app.services.marketing.providers.interfaces import EmailProvider, SMSProvider
from app.services.marketing.providers.mock import MockProvider
from app.services.marketing.providers.social import (
    InstagramGraphProvider,
    LinkedInProvider,
    MetaFacebookProvider,
    SocialMockProvider,
)
from app.services.marketing.providers.whatsapp import (
    WhatsAppMockProvider,
    WhatsAppProvider,
)


class MarketingProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, BaseMarketingProvider] = {}

    def register(self, provider: BaseMarketingProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str | None) -> BaseMarketingProvider | None:
        return self._providers.get(provider_id or "")

    def ids(self) -> list[str]:
        return sorted(self._providers)

    def summary(self) -> dict:
        return {
            provider_id: {
                "channel": provider.channel,
                "interface_only": provider.interface_only,
                "test_only": provider.test_only,
            }
            for provider_id, provider in sorted(self._providers.items())
        }


def build_provider_registry(settings: Settings) -> MarketingProviderRegistry:
    """Registry factory: real WhatsApp + email + social adapters always available; mock
    providers only when (and only when) the environment allows it."""
    registry = MarketingProviderRegistry()
    registry.register(WhatsAppProvider())
    registry.register(SMTPProvider())
    registry.register(GenericEmailAPIProvider())
    registry.register(AmazonSESEmailProvider())
    registry.register(EmailProvider())   # generic EMAIL interface (reporting id)
    registry.register(SMSProvider())
    # Phase 8: Social Media Providers
    registry.register(MetaFacebookProvider(
        app_id=getattr(settings, "META_APP_ID", None),
        app_secret=getattr(settings, "META_APP_SECRET", None),
    ))
    registry.register(InstagramGraphProvider(
        app_id=getattr(settings, "META_APP_ID", None),
        app_secret=getattr(settings, "META_APP_SECRET", None),
    ))
    registry.register(LinkedInProvider(
        client_id=getattr(settings, "LINKEDIN_CLIENT_ID", None),
        client_secret=getattr(settings, "LINKEDIN_CLIENT_SECRET", None),
    ))
    if settings.QBIT_ENV == "test" or (
        settings.QBIT_MARKETING_ALLOW_MOCK_PROVIDER
        and settings.QBIT_ENV != "production"
    ):
        registry.register(MockProvider())
        registry.register(WhatsAppMockProvider())
        registry.register(EmailMockProvider())
        registry.register(SocialMockProvider())
        registry.register(SocialMockProvider("FACEBOOK"))
        registry.register(SocialMockProvider("INSTAGRAM"))
        registry.register(SocialMockProvider("LINKEDIN"))
    return registry
