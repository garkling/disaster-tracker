import uuid
from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from pydantic import Field
from typing_extensions import Self
import googlemaps
import googleapiclient.discovery
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials as OAuthCredentials

from app.db import EventStorage
from app.model import ResultModel, ValidationError, PyObjectId
from app.pipeline import Pipeline, AsyncPipeline, as_result, Result, Ok, Nothing, Something, Option, call_as_result, Err
from app.settings import config


class Calendar(ResultModel):
    id: PyObjectId = Field(alias="_id", default=None)
    summary: str
    primary: bool


class CalendarEvent(ResultModel):
    id: PyObjectId = Field(alias="_id", default=None)
    summary: str
    location: str
    coordinates: tuple[float, float]
    creator: str
    start: datetime
    end: datetime
    link: str
    tracked: bool = False

    @classmethod
    @as_result(ValidationError, KeyError)
    def from_api(cls, obj: dict) -> Self:
        return cls(
            _id=obj['id'],
            summary=obj['summary'],
            location=obj['location'],
            coordinates=obj['coordinates'],
            creator=obj['creator']['email'],
            start=obj['start'].get('date') or obj['start']['dateTime'],
            end=obj['end'].get('date') or obj['end']['dateTime'],
            link=obj['htmlLink'],
            tracked=obj.get('tracked', False)
        )

    def __str__(self) -> str:
        return f"CalendarEvent({self.to_(dict).ok()})"


class CalendarTracker:

    def __init__(self, storage: EventStorage):
        self._storage = storage

    async def create_event(self, event: CalendarEvent) -> Result[bool, Exception]:
        return await self._storage.insert(event)

    async def create_or_update_event(self, event: CalendarEvent) -> Result[int, Exception]:
        return await self._storage.update_by_id(**event.to_(dict).ok())

    async def delete_event(self, event_id: str) -> Result[bool, Exception]:
        return await self._storage.delete(event_id)

    async def save_as_tracked(self, event: CalendarEvent) -> Result[bool, Exception]:
        return await event.update(tracked=True).then_async(self.create_or_update_event)

    async def sync_event(self, updated: CalendarEvent) -> Result[CalendarEvent, Exception]:
        match await self.get_event(updated.id):
            case Ok(Something(event)):
                ready = updated.update(tracked=event.tracked).ok()
                match await self.create_or_update_event(ready):
                    case Ok(_): return Ok(ready)
                    case err: return err

            case Ok(Nothing()):
                match await self.create_event(updated):
                    case Ok(_): return Ok(updated)
                    case err: return err

            case err: return err

    async def get_event(self, event_id: str) -> Result[Option[CalendarEvent], Exception]:
        match await self._storage.get_by_id(event_id):
            case Ok(Something(event_raw)):
                match CalendarEvent.from_(event_raw):
                    case Ok(event): return Ok(Something(event))
                    case err: return err

            case other : return other

    async def list_all_events(self, tracked: bool = False) -> Result[AsyncPipeline[CalendarEvent], Exception]:
        return (self._storage.find(tracked=tracked)).then(
            lambda gen: Ok(
                AsyncPipeline(gen).filter_map(lambda doc: CalendarEvent.from_(doc).ok())
            )
        )

    async def list_events_scheduled_in(self, days: int = 14, tracked: bool = False) -> Result[AsyncPipeline[CalendarEvent], Exception]:

        match call_as_result(Exception, func=lambda: datetime.combine(datetime.utcnow(), time.max)):
            case Ok(today):
                match self._storage.find(
                    start={"$lte": today + relativedelta(days=days),
                           "$gte": today},
                    tracked=tracked
                ):
                    case Ok(gen): return Ok(
                        AsyncPipeline(gen).filter_map(lambda doc: CalendarEvent.from_(doc).ok())
                    )

                    case err: return err
            case err: return err


class CalendarApi:

    def __init__(self, credentials: OAuthCredentials):
        self._calendar_api = googleapiclient.discovery.build(
            "calendar", "v3", credentials=credentials
        )
        self._google_maps_api = googlemaps.Client(config().geo_api_key)

    def list_calendars(self, **filters) -> Option[Pipeline[Calendar]]:

        match self._request_calendar_list(**filters):
            case Ok(response): return Something(
                Pipeline(response)
                .filter_map(lambda body: Calendar.from_(body).ok())
            )
            case _: return Nothing()

    @as_result(KeyError, HttpError)
    def get_event(self, calendar_id: str, event_id: str) -> Option[CalendarEvent]:

        match self._request_event(calendar_id, event_id):
            case Ok(response):
                match (
                    self._get_coordinates(response["location"])
                    .then(lambda coo: Ok({**response, "coordinates": coo}))
                    .then(CalendarEvent.from_api)
                ):
                    case Ok(event): return Something(event)
                    case err: return err.unwrap()

            case _: return Nothing()

    def list_events(self, calendar_id: str, **filters) -> Result[Pipeline[CalendarEvent], Exception]:

        match self._request_event_list(calendar_id, **filters):
            case Ok(events): return Ok(
                Pipeline(events)
                .filter(lambda raw_event: "location" in raw_event)
                .map(
                    lambda raw_event: self._get_coordinates(raw_event["location"])
                    .then(lambda coo: Ok({**raw_event, "coordinates": coo}))
                    .then(lambda body: CalendarEvent.from_api(body))
                )
                .filter_map(lambda event_result: event_result.ok())
            )

            case err: return err

    @as_result(HttpError)
    def _request_event(self, calendar_id: str, event_id: str) -> dict:
        return (
            self._calendar_api
            .events()
            .get(calendarId=calendar_id, eventId=event_id)
            .execute()
        )

    @as_result(AttributeError, HttpError)
    def _request_event_list(self, calendar_id: str, **filters) -> list[dict]:
        # todo: make async
        response = (
            self._calendar_api
            .events()
            .list(calendarId=calendar_id, **filters)
            .execute()
        )

        return response.get("items", [])

    @as_result(HttpError)
    def _request_calendar_list(self, **filters) -> list[dict]:
        response = (
            self._calendar_api
            .calendarList()
            .list(**filters)
            .execute()
        )

        return response.get("items", [])

    @as_result(HttpError)
    def watch_calendar(self, calendar_id: str) -> str:
        print(calendar_id)
        channel_id = str(uuid.uuid4())
        (
            self._calendar_api
            .events()
            .watch(
                calendarId=calendar_id,
                body=dict(
                    id=channel_id,
                    type="web_hook",
                    address=f"http://{config().api_host}:{config().api_port}/{config().event_listener_route}"
                )

            ).execute()
        )

        return channel_id

    @as_result(HttpError)
    def unwatch_calendar(self, calendar_id: str, channel_id: str):
        return (
            self._calendar_api
            .channels()
            .stop(
                id=channel_id,
                resourceId=calendar_id
            )
        )

    @as_result(KeyError, IndexError, AttributeError, googlemaps.exceptions.HTTPError)
    def _get_coordinates(self, location_str: str) -> tuple:
        geocode = self._google_maps_api.geocode(location_str)
        coo = geocode[0]['geometry']['location']
        return coo['lat'], coo['lng']
