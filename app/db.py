from __future__ import annotations

from datetime import timedelta
from functools import wraps
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Union, Optional, AsyncIterator, Iterable

import aioredis
from aioredis import RedisError
from pymongo import UpdateOne
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient

from app.pipeline import as_result, as_async_result, Something, Nothing, Option, Pipeline
from app.settings import config


class QuickStorage:

    Literal = Union[bytes, str, int, float]
    KeyType = Union[bytes, str, memoryview]

    @staticmethod
    def _inject_client(wrapped):

        @wraps(wrapped)
        async def wrapper(self: QuickStorage, *args, client: Optional[aioredis.Redis] = None, **kwargs):
            match client:
                case None:
                    async with self.connect() as client:
                        return await wrapped(self, *args, client=client, **kwargs)

                case client:
                    return await wrapped(self, *args, client=client, **kwargs)

        return wrapper

    @staticmethod
    def prefix(wrapped):

        @wraps(wrapped)
        async def wrapper(self, key, *args, **kwargs):
            return await wrapped(self, self._prefix + key, *args, **kwargs)

        return wrapper

    def __init__(self, prefix: str = ""):
        self._pool = aioredis.ConnectionPool(
            max_connections=config().redis_pool_size,
            host=config().redis_host,
            port=config().redis_port,
            username=config().redis_user,
            password=config().redis_pass,
            decode_responses=True
        )
        self._prefix = prefix

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aioredis.Redis]:
        conn = aioredis.Redis(connection_pool=self._pool)
        yield conn
        await conn.close()

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def set(self, key: KeyType, value: Literal, *, client: aioredis.Redis) -> bool:
        return await client.set(key, value)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def set_map(self, key: KeyType, mapping: dict, *, client: aioredis.Redis) -> int:
        return await client.hset(key, mapping=mapping)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def set_expiry(self, key: KeyType, time: int | timedelta, *, client: aioredis.Redis) -> bool:
        return await client.expire(key, time)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def remove_expiry(self, key: KeyType, *, client: aioredis.Redis) -> bool:
        return await client.persist(key)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def get(self, key: KeyType, *, client: aioredis.Redis) -> Option[Literal]:
        match await client.get(key):
            case None: return Nothing()
            case val: return Something(val)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def get_map(self, key: KeyType, *, client: aioredis.Redis) -> Option[Literal]:
        match await client.hgetall(key):
            case None: return Nothing()
            case val: return Something(val)

    @as_async_result(RedisError)
    @_inject_client
    @prefix
    async def delete(self, key: KeyType, *, client: aioredis.Redis) -> int:
        return await client.delete(key)


class EventStorage:

    def __init__(self, collection: str):

        self._client = AsyncIOMotorClient(
            host=config().mongo_host,
            port=config().mongo_port,
            username=config().mongo_user,
            password=config().mongo_pass,
            authSource=config().mongo_db,
        )

        self._db = self._client.get_database(config().mongo_db)
        self._collection = self._db.get_collection(collection)

    @as_async_result(PyMongoError, AttributeError)
    async def insert(self, var: dict | Iterable[dict]) -> bool:
        match var:
            case dict():
                return (await self._collection.insert_one(var)).acknowledged
            case _ if isinstance(var, Iterable):
                return (await self._collection.insert_many(var)).acknowledged

            case _: raise TypeError(f"Incompatible `body` for EventStorage")

    @as_async_result(PyMongoError, AttributeError)
    async def delete(self, _id: str) -> bool:
        res = await self._collection.delete_one({"_id": _id})
        return res.acknowledged

    @as_async_result(PyMongoError, AttributeError)
    async def update_by_id(self, _id: str, **fields) -> int:
        return (await self._collection.update_one(
            filter={"_id": _id},
            update={"$set": fields},
            upsert=True
        )).matched_count

    @as_async_result(PyMongoError, AttributeError)
    async def update_many(self, objs: Iterable[dict]) -> dict:
        match Pipeline(objs).map(
            lambda obj: UpdateOne({"_id": obj["_id"]}, {"$set": obj}, upsert=True)
        ).collect_as(list):

            case Something(ops):
                return (await self._collection.bulk_write(ops)).bulk_api_result

            case _: return dict()

    @as_async_result(PyMongoError, AttributeError)
    async def get_by_id(self, _id: str) -> Option[dict]:
        match await self._collection.find_one({"_id": _id}):
            case dict(doc): return Something(doc)
            case _: return Nothing()

    @as_result(PyMongoError, AttributeError)  # type: ignore
    async def find(self, **by) -> AsyncGenerator[dict, None]:
        print(by)
        async for doc in self._collection.find(by):
            if doc:
                yield doc
