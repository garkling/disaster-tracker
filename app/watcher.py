from __future__ import annotations

from pydantic import BaseModel

from app.db import QuickStorage
from app.model import ResultModel
from app.pipeline import Ok, Result, Option, Something, Nothing


class WatchBody(BaseModel):
    calendar_id: str


class ChannelInfo(ResultModel):
    id: str
    user_email: str
    calendar_id: str


class EventChannels:
    """Maps Google API Channel ID to user email/calendar"""

    def __init__(self, storage: QuickStorage | None = None):
        self._storage = storage or QuickStorage(prefix="PREFERENCES:")

    async def create(self, channel_info: ChannelInfo) -> Result[int, Exception]:
        return await self._storage.set_map(
            channel_info.id,
            mapping=channel_info.to_(dict, exclude={"id"})
        )

    async def get(self, channel_id: str) -> Result[Option[ChannelInfo], Exception]:
        obj_result: Option[dict]

        match await self._storage.get_map(channel_id):
            case Ok(obj_result):
                match obj_result:
                    case Something(obj): return ChannelInfo.from_(obj)
                    case _: return Ok(Nothing())

            case err: return err

    async def delete(self, channel_id: str) -> Result[int, Exception]:
        return await self._storage.delete(channel_id)

    async def pop(self, channel_id: str) -> Result[Option[ChannelInfo], Exception]:
        async with self._storage.connect() as client:
            match await self._storage.get_map(channel_id, client=client):
                case Ok(obj_result):
                    match obj_result:
                        case Something(obj):
                            await self._storage.delete(channel_id)
                            return ChannelInfo.from_(obj)
                        case _:
                            return Ok(Nothing())

                case err:
                    return err


class WatchedCalendars:
    """Maps Calendar ID to Channel ID"""

    def __init__(self, storage: QuickStorage | None = None):
        self._storage = storage or QuickStorage(prefix="CALENDAR:")

    async def create(self, calendar_id: str, channel_id: str) -> Result[int, Exception]:
        return await self._storage.set(
            calendar_id,
            channel_id
        )

    async def get(self, calendar_id: str) -> Result[Option[str], Exception]:
        match await self._storage.get(calendar_id):
            case Ok(Something(obj)):
                return ChannelInfo.from_(obj)

            case other: return other

    async def delete(self, calendar_id: str) -> Result[int, Exception]:
        return await self._storage.delete(calendar_id)
