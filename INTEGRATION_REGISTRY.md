# QBIT GROWTH OS — INTEGRATION REGISTRY
**Company:** QbitPro India Pvt Ltd.  
**Standard:** Real functionality only. No fake integrations, no fabricated mock responses in production, clear NOT CONNECTED status until verified.

---

## 1. Integration Status Matrix

| Integration ID | Service / Provider | Purpose | Authentication Mechanism | Required Credentials / Secrets | Real Health Check Endpoint | Default State Without Keys |
|---|---|---|---|---|---|---|
| **INT-01** | **Apify Platform** | Cloud Actor Execution & Datasets | Bearer API Token | `APIFY_TOKEN` | `GET https://api.apify.com/v2/users/me` | `NOT_CONNECTED` (fallback to local HTTP/Playwright scrapers) |
| **INT-02** | **Google Maps Places API** | Local business lookup & geocoding | API Key | `GOOGLE_MAPS_API_KEY` | `GET https://maps.googleapis.com/maps/api/place/nearbysearch/json` | `NOT_CONNECTED` (fallback to public web reconnaissance) |
| **INT-03** | **WhatsApp Business Cloud API** | Official multi-template messaging | System User Permanent Access Token + WABA ID | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WABA_ID` | `GET https://graph.facebook.com/v20.0/{phone_number_id}` | `NOT_CONNECTED` (Strictly blocks sends, alerts admin) |
| **INT-04** | **SMTP / Email Delivery** | Corporate transactional & cold outreach | Basic Auth / TLS / API Keys (Resend / SendGrid / Postmark) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Real SMTP `EHLO` handshake & TLS socket connect | `NOT_CONNECTED` (campaigns held in draft) |
| **INT-05** | **Meta Graph API (Facebook/Instagram)** | Public business page & post intelligence | OAuth 2.0 App Token / User Token | `META_APP_ID`, `META_APP_SECRET` | `GET https://graph.facebook.com/v20.0/debug_token` | `NOT_CONNECTED` |
| **INT-06** | **LinkedIn API** | Authorized company page actions & public insights | OAuth 2.0 Authorization Code | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | `GET https://api.linkedin.com/v2/userinfo` | `NOT_CONNECTED` |
| **INT-07** | **IndiaMART B2B Gateway** | Public B2B directory intelligence | CRM API Key / Web Session | `INDIAMART_API_KEY` | Real provider ping / schema check | `NOT_CONNECTED` |
| **INT-08** | **Justdial Local Gateway** | Local business directory intelligence | Public Crawler Session | `JUSTDIAL_SESSION_CONFIG` | Real proxy & endpoint connectivity check | `NOT_CONNECTED` |
| **INT-09** | **Gemini AI / LLM Engine** | Automated lead research & email copy assistant | API Key | `GEMINI_API_KEY` | `POST https://generativelanguage.googleapis.com/v1beta/models` | `NOT_CONNECTED` (disables AI synthesis tabs) |
| **INT-10** | **OpenAI API** | Secondary AI reasoning & entity extraction | Bearer API Token | `OPENAI_API_KEY` | `GET https://api.openai.com/v1/models` | `NOT_CONNECTED` |
| **INT-11** | **Google Drive / Cloud Storage** | Lead export backup & artifact storage | Service Account JSON Key | `GOOGLE_SERVICE_ACCOUNT_KEY` | Google Drive API `files.list` probe | `NOT_CONNECTED` (stores to local encrypted `/qbit-data`) |

---

## 2. Integration Adapter Contract

Every integration follows a strict lifecycle contract implemented in `app/services/marketing/providers/` and `app/scrapers/core/`:

```python
class IntegrationAdapter(Protocol):
    async def validate_credentials(self, credentials: dict) -> HealthProbeResult:
        """Runs a real probe against the third-party API without mutating state."""
        ...

    async def get_connection_status(self) -> ConnectionStatus:
        """Returns CONNECTED, INVALID_CREDENTIALS, RATE_LIMITED, or NOT_CONNECTED."""
        ...

    async def execute_action(self, payload: dict) -> ProviderResult:
        """Executes the action only if get_connection_status() is CONNECTED."""
        ...
```

---

## 3. Compliance & Safety Invariants

1. **No Scraping of Private Data:** Scrapers extract only publicly indexable business data (company name, public phone, public address, official website, public reviews). Private profiles, DMs, private stories, and restricted personal information are strictly forbidden.
2. **WhatsApp Policy Adherence:** WhatsApp bulk extraction is prohibited. WhatsApp outreach is restricted to the official WhatsApp Business Platform with approved message templates and consent-based audience management.
3. **Secret Storage Hardening:** All provider tokens and credentials are encrypted at rest using AES-GCM-256 before storage in the database. Raw secrets are never echoed in API logs, frontend responses, or audit payloads.
