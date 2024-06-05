from datetime import datetime
from typing import Iterable

import httpx
from pydantic import ValidationError, Field
from typing_extensions import Self

from app import utils
from app.db import EventStorage
from app.calendar_events import CalendarEvent
from app.model import ResultModel, PyObjectId
from app.pipeline import Option, Ok, Nothing, Something, as_async_result, Pipeline, as_result, AsyncPipeline, Result


class DisasterCategory(ResultModel):
    id: PyObjectId = Field(alias="_id", default=None)
    title: str
    description: str

    @classmethod
    @as_result(ValidationError, KeyError)
    def from_eonet(cls, obj: dict) -> Self:
        return cls(
            _id=obj["id"],
            title=obj["title"],
            description=obj.get("description") or obj["title"]
        )


class DisasterEvent(ResultModel):
    id: PyObjectId = Field(alias="_id", default=None)
    name: str
    description: str = ""
    link: str
    categories: tuple[DisasterCategory]
    active: bool
    lat: float
    lon: float
    most_recent_date: datetime
    magnitude: str

    @classmethod
    @as_result(ValidationError, KeyError)
    def from_eonet(cls, obj: dict) -> Self:
        latest_info = obj["geometry"][-1]
        return cls(
            _id=obj["id"],
            name=obj["title"],
            description=obj["description"] or "",
            link=obj["link"],
            categories=tuple(DisasterCategory.from_eonet(cat_obj).unwrap() for cat_obj in obj["categories"]),
            active=obj["closed"] or True,
            lat=latest_info["coordinates"][1],
            lon=latest_info["coordinates"][0],
            most_recent_date=latest_info["date"],
            magnitude=cls._convert_magnitudes(obj)
        )

    @staticmethod
    def _convert_magnitudes(obj: dict):
        value = str(obj.get("magnitudeValue") or "")
        unit = str(obj.get("magnitudeUnit") or "")

        return value + (" " if value and unit else "") + unit


class DisasterAlert(ResultModel):
    recipient: str
    event: CalendarEvent
    disasters: tuple[DisasterEvent]
    dangerous: bool = True

    def __str__(self) -> str:
        return (f"Disaster alert("
                f"to: {self.recipient}, "
                f"calendar_e: {self.event.id}, "
                f"disaster_e: {tuple(d.id for d in self.disasters)})"
                )


class NasaEonetEventAPI:

    API_URL = "https://eonet.gsfc.nasa.gov/api/v3"

    @as_async_result(httpx.HTTPError)
    async def fetch_events(self, active: bool = True, categories: Iterable = ()) -> tuple[DisasterEvent]:
        params = httpx.QueryParams(
            status="open" if active else "closed",
            category=",".join(categories)
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.API_URL}/events", params=params)
            return Pipeline(
                response.json()["events"]
            ).filter_map(
                lambda obj: DisasterEvent.from_eonet(obj).ok()
            ).collect_as(tuple).something() or ()

    @as_async_result(httpx.HTTPError)
    async def fetch_event(self, event_id: str) -> Option[DisasterEvent]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.API_URL}/events/{event_id}")
            if obj := response.json():
                match DisasterEvent.from_eonet(obj):
                    case Ok(event): return Something(event)

            return Nothing()

    @as_async_result(httpx.HTTPError)
    async def fetch_categories(self) -> Pipeline[DisasterCategory]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.API_URL}/categories")
            return Pipeline(
                response.json()["categories"]
            ).filter_map(
                lambda obj: DisasterCategory.from_eonet(obj).ok()
            )

    @as_async_result(httpx.HTTPError)
    async def fetch_category_events(self, category, active: bool = True) -> Pipeline[DisasterEvent]:
        params = httpx.QueryParams(
            status="open" if active else "closed",
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.API_URL}/categories/{category}", params=params)
            return Pipeline(
                response.json()["events"]
            ).filter_map(
                lambda obj: DisasterEvent.from_eonet(obj).ok()
            )


class DisasterTracker:

    _DISASTER_DISTANCE_THRESHOLD_KM = 500

    def __init__(self, storage: EventStorage):
        self._storage = storage

    async def save_many(self, disasters: list[DisasterEvent]) -> Result[dict, Exception]:
        match Pipeline(disasters).filter_map(
            lambda d: d.to_(dict).ok()
        ).collect_as(list):

            case Something(disaster_list):
                return await self._storage.update_many(disaster_list)

            case _: return Ok(dict())

    async def check_calendar_event(self, event: CalendarEvent) -> Option[DisasterAlert]:
        match await self.scan_active_disasters(event.coordinates):
            case Something(disasters): return Something(
                DisasterAlert(
                    recipient=event.creator,
                    event=event,
                    disasters=disasters
            ))

        return Nothing()

    async def scan_active_disasters(self, point: tuple[float, float]) -> Option[tuple[DisasterEvent]]:
        northeast = utils.calc_destination_point_by(point, self._DISASTER_DISTANCE_THRESHOLD_KM, bearing=45)
        southwest = utils.calc_destination_point_by(point, self._DISASTER_DISTANCE_THRESHOLD_KM, bearing=225)

        max_lat, max_lon = northeast
        min_lat, min_lon = southwest

        match self._storage.find(
            lat={"$gte": min_lat, "$lte": max_lat},
            lon={"$gte": min_lon, "$lte": max_lon},
            active=True
        ):
            case Ok(disaster_gen):
                return await AsyncPipeline(disaster_gen).filter_map(
                    lambda obj: DisasterEvent.from_(obj).ok()
                ).collect_as(tuple)

        return Nothing()
