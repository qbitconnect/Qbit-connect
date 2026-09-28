"""Contact Lists API schemas (Phase 6 / Brief §15)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContactListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class ContactListUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class AddMembersRequest(BaseModel):
    lead_ids: list[str] = Field(..., min_length=1)


class CSVImportRequest(BaseModel):
    csv_content: str = Field(..., min_length=1)
    has_consent: bool = Field(default=False)
    consent_source: str | None = Field(default=None, max_length=200)
