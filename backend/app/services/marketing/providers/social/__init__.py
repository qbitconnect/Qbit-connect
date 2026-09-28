"""Social media provider registry package (Phase 8)."""

from app.services.marketing.providers.social.base import (
    BaseSocialProvider,
    SocialAnalyticsResult,
    SocialPublishResult,
)
from app.services.marketing.providers.social.facebook import MetaFacebookProvider
from app.services.marketing.providers.social.instagram import InstagramGraphProvider
from app.services.marketing.providers.social.linkedin import LinkedInProvider
from app.services.marketing.providers.social.mock import SocialMockProvider

__all__ = [
    "BaseSocialProvider",
    "SocialAnalyticsResult",
    "SocialPublishResult",
    "MetaFacebookProvider",
    "InstagramGraphProvider",
    "LinkedInProvider",
    "SocialMockProvider",
]
