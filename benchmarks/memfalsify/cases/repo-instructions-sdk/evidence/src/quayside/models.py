"""Response models. Unknown fields are ignored so that new API fields do not break older SDK releases."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Status(str, Enum):
    CREATED = "created"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RETURNED = "returned"


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    at: datetime
    location: str
    description: str


class Shipment(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tracking_id: str
    carrier: str
    status: Status
    events: list[Event] = []
