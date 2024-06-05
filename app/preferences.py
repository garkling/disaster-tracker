from __future__ import annotations

from app.db import QuickStorage
from app.model import ResultModel
from app.pipeline import Result, Ok, Something, Nothing


class Preferences(ResultModel):
    calendars: tuple[str] = ("primary", )
    disaster_categories: tuple[str] = ("all", )


class UserPreferences:
    """Maps user email to user preferences obj"""

    def __init__(self, storage: QuickStorage | None = None):
        self._storage = storage or QuickStorage(prefix="PREFERENCES:")

    async def create(self, email: str, pref: Preferences) -> Result[int, Exception]:
        return await (
            pref.to_(str)
            .then_async(
                lambda dumped: self._storage.set(email, dumped)
            )
        )

    async def get(self, email: str) -> Result[Preferences, Exception]:
        match await self._storage.get(email):
            case Ok(Something(pref)):
                return Preferences.from_(pref)

            case Ok(Nothing()) : return Ok(Preferences())
            case err: return err

    async def pop(self, email: str) -> Result[Preferences, Exception]:
        async with self._storage.connect() as client:
            match await self._storage.get(email, client=client):
                case Ok(Something(pref)):
                    await self._storage.delete(email, client=client)
                    return Preferences.from_(pref)

                case Ok(Nothing()): return Ok(Preferences())
                case err: return err
