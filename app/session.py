import uuid

from app.db import QuickStorage
from app.pipeline import Ok, Err, Result, Something, Option


class ClientSession:
    """Maps session UUID to user email"""

    SESSION_EXP = 3600 * 24 * 182

    def __init__(self, storage: QuickStorage | None = None):
        self._storage = storage or QuickStorage()

    async def create(self, email: str) -> Result[str, Exception]:
        session_id = str(uuid.uuid4())
        async with self._storage.connect() as client:
            match await self._storage.set(session_id, email, client=client):
                case Ok(True):
                    await self._storage.set_expiry(session_id, self.SESSION_EXP, client=client)
                    return Ok(session_id)

                case Ok(False): return Err(IOError("User session hasn't been saved"))
                case err: return err

    async def get(self, session_id) -> Result[Option[str], Exception]:
        async with self._storage.connect() as client:
            match await self._storage.get(session_id, client=client):
                case Ok(Something(user_email)):
                    await self._storage.set_expiry(session_id, self.SESSION_EXP, client=client)
                    return Ok(Something(user_email))

                case other: return other

    async def delete(self, session_id: str) -> Result[int, Exception]:
        return await self._storage.delete(session_id)
