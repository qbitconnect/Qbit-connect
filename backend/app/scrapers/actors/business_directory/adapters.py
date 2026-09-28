"""Directory source adapters (brief §29).

    BusinessDirectoryActor
        ├── GenericDirectoryAdapter (declarative CSS selectors)
        └── future adapters: implement DirectorySource + register in
            ADAPTER_REGISTRY (one line, no core changes)

Each adapter DECLARES its contract (source_name, base rules, input shape) and
implements `pages()` / `parse()`. Adapters never bypass access controls and
never evade platform restrictions (§17).
"""

from __future__ import annotations

from typing import Iterator, Protocol

from bs4 import BeautifulSoup

from app.scrapers.actors.business_directory.schemas import DirectoryAdapterConfig
from app.scrapers.core.exceptions import ScraperConfigurationError
from app.scrapers.core.netguard import canonical_url, normalize_url

#: canonical lead fields an adapter may fill
ALLOWED_FIELDS = (
    "business_name", "phone", "email", "website", "address", "city",
    "state", "country", "category",
)


class DirectorySource(Protocol):
    source_name: str

    def pages(self, config: DirectoryAdapterConfig) -> Iterator[str]:
        """Yield listing-page URLs (validated by the caller's netguard)."""
        ...

    def parse(
        self, config: DirectoryAdapterConfig, soup: BeautifulSoup, page_url: str
    ) -> list[dict]:
        """Parse one listing page into raw lead dicts."""
        ...


class GenericDirectoryAdapter:
    """Declarative adapter: the operator supplies CSS selectors in the job
    input; no hardcoded websites. Deterministic and inspectable (§30 spirit,
    §29 mechanics)."""

    source_name = "generic"

    def pages(self, config: DirectoryAdapterConfig) -> Iterator[str]:
        yield str(config.list_url)

    def extract(
        self, html_or_soup: str | BeautifulSoup, page_url: str, config: dict | DirectoryAdapterConfig | None = None
    ) -> list[dict]:
        if isinstance(html_or_soup, str):
            soup = BeautifulSoup(html_or_soup, "html.parser")
        else:
            soup = html_or_soup
        if config is None:
            cfg = DirectoryAdapterConfig(list_url=page_url)
        elif isinstance(config, dict):
            cfg_dict = dict(config)
            item_sel = cfg_dict.pop("item_selector", "")
            fields = cfg_dict.pop("fields", {})
            for key, mapped in [
                ("name_selector", "business_name"),
                ("phone_selector", "phone"),
                ("email_selector", "email"),
                ("address_selector", "address"),
                ("website_selector", "website"),
            ]:
                if key in cfg_dict:
                    fields[mapped] = cfg_dict.pop(key)
            cfg = DirectoryAdapterConfig(
                list_url=page_url,
                item_selector=item_sel,
                fields=fields,
                field_attributes=cfg_dict.get("field_attributes", {}),
            )
        else:
            cfg = config
        return self.parse(cfg, soup, page_url)

    def parse(
        self, config: DirectoryAdapterConfig, soup: BeautifulSoup, page_url: str
    ) -> list[dict]:
        items: list[dict] = []
        for node in soup.select(config.item_selector)[:500]:
            record: dict = {}
            for field, selector in config.fields.items():
                if field not in ALLOWED_FIELDS:
                    continue
                element = node.select_one(selector)
                if element is None:
                    continue
                attribute = config.field_attributes.get(field)
                if not attribute:
                    attribute = "href" if (field == "website" and element.name == "a") else "text"
                if attribute == "text":
                    value = element.get_text(" ", strip=True)
                else:
                    value = element.get(attribute) or ""
                value = str(value).strip()
                if value:
                    record[field] = value[:1000]
            if record:
                record.setdefault("metadata_page", page_url)
                items.append(record)
        return items

    def next_page(
        self, config: DirectoryAdapterConfig, soup: BeautifulSoup, base_url: str
    ) -> str | None:
        """Resolve the next-page link against the CURRENT page URL (not the
        configured list_url) so relative hrefs keep working on page 2+."""
        if not config.pagination_next_selector:
            return None
        tag = soup.select_one(config.pagination_next_selector)
        if tag is None or not tag.get("href"):
            return None
        return normalize_url(base_url, str(tag["href"]))


class JsonLdDirectoryAdapter:
    """Extracts Schema.org LocalBusiness and Organization structured data
    from compliant business directory pages."""

    source_name = "jsonld"

    def pages(self, config: DirectoryAdapterConfig) -> Iterator[str]:
        yield str(config.list_url)

    def extract(
        self, html_or_soup: str | BeautifulSoup, page_url: str, config: dict | DirectoryAdapterConfig | None = None
    ) -> list[dict]:
        if isinstance(html_or_soup, str):
            soup = BeautifulSoup(html_or_soup, "html.parser")
        else:
            soup = html_or_soup
        if config is None or isinstance(config, dict):
            cfg = DirectoryAdapterConfig(list_url=page_url)
        else:
            cfg = config
        return self.parse(cfg, soup, page_url)

    def parse(
        self, config: DirectoryAdapterConfig, soup: BeautifulSoup, page_url: str
    ) -> list[dict]:
        import json

        items: list[dict] = []
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string.strip())
            except Exception:
                continue

            entries = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
                entries = data["@graph"]

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                etype = str(entry.get("@type", "")).lower()
                # LocalBusiness, Store, Restaurant, ProfessionalService, Organization
                if not any(k in etype for k in ("business", "organization", "store", "restaurant", "service", "company")):
                    continue

                name = entry.get("name") or entry.get("legalName")
                if not name:
                    continue

                address = ""
                city = ""
                state = ""
                country = ""
                addr_obj = entry.get("address")
                if isinstance(addr_obj, dict):
                    parts = [
                        addr_obj.get("streetAddress"),
                        addr_obj.get("addressLocality"),
                        addr_obj.get("addressRegion"),
                        addr_obj.get("postalCode"),
                        addr_obj.get("addressCountry"),
                    ]
                    address = ", ".join(p.strip() for p in parts if p and str(p).strip())
                    city = addr_obj.get("addressLocality") or ""
                    state = addr_obj.get("addressRegion") or ""
                    country = addr_obj.get("addressCountry") or ""
                elif isinstance(addr_obj, str):
                    address = addr_obj

                rating = None
                review_count = None
                agg = entry.get("aggregateRating")
                if isinstance(agg, dict):
                    try:
                        rating = float(agg.get("ratingValue"))
                    except (ValueError, TypeError):
                        pass
                    try:
                        review_count = int(agg.get("reviewCount") or agg.get("ratingCount"))
                    except (ValueError, TypeError):
                        pass

                record = {
                    "business_name": str(name).strip()[:300],
                    "phone": str(entry.get("telephone", "")).strip() or None,
                    "email": str(entry.get("email", "")).strip() or None,
                    "website": str(entry.get("url") or entry.get("sameAs") or "").strip() or None,
                    "address": address or None,
                    "city": city or None,
                    "state": state or None,
                    "country": country or None,
                    "category": entry.get("@type") or "LocalBusiness",
                    "rating": rating,
                    "review_count": review_count,
                    "metadata": {
                        "schema_type": entry.get("@type"),
                        "page_url": page_url,
                    },
                }
                items.append(record)
        return items

    def next_page(
        self, config: DirectoryAdapterConfig, soup: BeautifulSoup, base_url: str
    ) -> str | None:
        if not config.pagination_next_selector:
            return None
        tag = soup.select_one(config.pagination_next_selector)
        if tag is None or not tag.get("href"):
            return None
        return normalize_url(base_url, str(tag["href"]))


ADAPTER_REGISTRY: dict[str, type] = {
    "generic": GenericDirectoryAdapter,
    "jsonld": JsonLdDirectoryAdapter,
}


def get_adapter(name: str):
    adapter_cls = ADAPTER_REGISTRY.get(name.strip().lower())
    if adapter_cls is None:
        raise ScraperConfigurationError(
            f"Unknown directory adapter {name!r}. Available: {sorted(ADAPTER_REGISTRY)}"
        )
    return adapter_cls()


__all__ = [
    "ADAPTER_REGISTRY",
    "DirectorySource",
    "GenericDirectoryAdapter",
    "JsonLdDirectoryAdapter",
    "canonical_url",
    "get_adapter",
]
