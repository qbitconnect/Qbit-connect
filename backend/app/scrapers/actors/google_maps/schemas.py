"""Google Maps actor schemas (brief §8, §28)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class GoogleMapsInput(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Search term, category query, or full Google Maps URL")
    category: str | None = Field(default=None, max_length=150, description="Business category filter (e.g. 'Hotels', 'Restaurants')")
    city: str | None = Field(default=None, max_length=100, description="City name")
    state: str | None = Field(default=None, max_length=100, description="State or province")
    country: str | None = Field(default=None, max_length=100, description="Country name or code")
    region: str | None = Field(default=None, max_length=50, description="Country/region code (e.g. 'us', 'in')")
    radius_meters: int | None = Field(default=None, ge=100, le=100000, description="Search radius in meters (100 to 100,000)")
    max_results: int = Field(default=50, ge=1, le=5000, description="Maximum number of places to retrieve")
    language: str | None = Field(default=None, max_length=10, description="Language code (e.g. 'en')")
    drop_duplicates: bool = Field(default=True, description="Drop duplicate listings from provider")

    @field_validator("query")
    @classmethod
    def _clean_query(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Query must contain non-whitespace characters")
        if "\0" in s:
            raise ValueError("Query contains invalid null byte characters")
        return s

    @field_validator("category", "city", "state", "country", "region", "language")
    @classmethod
    def _clean_optional_str(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if "\0" in s:
            raise ValueError("Field contains invalid null byte characters")
        return s


OUTPUT_FIELDS = (
    "business_name",
    "category",
    "phone",
    "email",
    "website",
    "address",
    "city",
    "state",
    "country",
    "source",
    "source_url",
    "rating",
    "review_count",
    "metadata",
    "scraped_at",
)
