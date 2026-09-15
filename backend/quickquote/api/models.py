"""Request models for the JSON API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ProductIn(BaseModel):
    customer: str | None = None
    brand: str | None = None
    formula_name: str | None = None
    customer_sku: str | None = None
    dosage_form: str | None = "Capsule"
    capsule_size: str | None = None
    capsule_type: str | None = None
    serving_size: str | None = None
    servings_per_bottle: int | None = Field(default=None, ge=1)
    count_per_bottle: int | None = Field(default=None, ge=1)
    annual_volume_bottles: int | None = Field(default=None, ge=1)
    moq: int | None = Field(default=None, ge=0)
    timeline: str | None = None
    machine: str | None = None
    mfg_loss_factor: float | None = Field(default=None, gt=0)
    claims: list[str] = Field(default_factory=list)
    testing: list[str] = Field(default_factory=list)


class FormulaLineIn(BaseModel):
    name: str
    claimed_mg: float | None = Field(default=None, ge=0)
    part_code: str | None = None


class PackagingLineIn(BaseModel):
    role: str
    description: str
    qty_per_bottle: float | None = Field(default=None, ge=0)
    part_code: str | None = None
    listed_unit_cost: float | None = Field(default=None, ge=0)


class QuoteIn(BaseModel):
    """A quote built in the sales-rep interface rather than uploaded."""

    product: ProductIn = Field(default_factory=ProductIn)
    formula: list[FormulaLineIn] = Field(default_factory=list)
    packaging: list[PackagingLineIn] = Field(default_factory=list)
