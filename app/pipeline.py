from __future__ import annotations

import inspect
import asyncio
from typing import Iterable, TypeVar, Callable, AsyncGenerator, Awaitable, Generic, Union, ParamSpec, Type, TypeAlias, cast, TypeGuard


T = TypeVar('T')
U = TypeVar('U')
E = TypeVar('E')
EX = TypeVar('EX', bound=Exception)

P = ParamSpec('P')
R = TypeVar('R')

Collection = Union[list[T], tuple[T, ...], set[T]]
CollectionType = Union[type[list], type[tuple], type[set]]


class Something(Generic[T]):

    __match_args__ = ("_value", )
    __slots__ = ("_value", )

    def __init__(self, value: T):
        self._value = value

    @staticmethod
    def is_something() -> bool:
        return True

    @staticmethod
    def is_nothing() -> bool:
        return False

    def something(self):
        return self._value

    def then(self, func: Callable[[T], Option]) -> Option:
        return func(self._value)
    
    async def then_async(self, _func: Callable[[T], Awaitable[Option]]) -> Option:
        return await _func(self._value)

    def __eq__(self, other) -> bool:
        return isinstance(other, Something) and self._value == other._value

    def __ne__(self, other) -> bool:
        return not (self == other)

    def __hash__(self) -> int:
        return hash((True, self._value))

    def __repr__(self) -> str:
        return f"Something({self._value!r})"


class Nothing:

    __slots__ = ()

    @staticmethod
    def is_something() -> bool:
        return False

    @staticmethod
    def is_nothing() -> bool:
        return True

    @staticmethod
    def something():
        return None

    def then(self, _func: Callable[[T], Option]) -> Nothing:
        return self
    
    async def then_async(self, _func: Callable[[T], Awaitable[Option]]) -> Nothing:
        return self

    def __eq__(self, other) -> bool:
        return isinstance(other, Nothing)

    def __ne__(self, other) -> bool:
        return not isinstance(other, Nothing)

    def __hash__(self) -> int:
        return hash((False, 74753280084933697089264224702766))

    def __repr__(self) -> str:
        return "Nothing()"


class Ok(Generic[T]):

    __match_args__ = ("ok_value", )
    __slots__ = ("_value", )

    def __init__(self, value: T):
        self._value = value

    @staticmethod
    def is_ok() -> bool:
        return True

    @staticmethod
    def is_err() -> bool:
        return False

    def ok(self) -> T:
        return self._value

    @property
    def ok_value(self) -> T:
        return self._value

    @staticmethod
    def err():
        return None

    def unwrap(self):
        return self._value

    def unwrap_or(self, _other: T) -> T:
        return self._value

    def then(self, func: Callable[[T], Result]) -> Result:
        return func(self._value)

    async def then_async(self, _func: Callable[[T], Awaitable[Result]]) -> Result:
        return await _func(self._value)

    def __eq__(self, other) -> bool:
        return isinstance(other, Ok) and self._value == other._value

    def __ne__(self, other) -> bool:
        return not (self == other)

    def __hash__(self) -> int:
        return hash((True, self._value))

    def __repr__(self) -> str:
        return f'Ok({self._value!r})'


class Err(Generic[E]):

    __match_args__ = ("err_value", )
    __slots__ = ("_error", )

    def __init__(self, error: E):
        self._error = error

    @staticmethod
    def is_ok() -> bool:
        return False

    @staticmethod
    def is_err() -> bool:
        return True

    @staticmethod
    def ok():
        return None

    def err(self) -> E:
        return self._error

    @property
    def err_value(self) -> E:
        return self._error

    def unwrap(self):
        raise UnwrapError(
            self,
            f"Called `Result.unwrap()` with `Err` value: {self._error!r}"

        ) from self._error

    @staticmethod
    def unwrap_or(other: T) -> T:
        return other

    def then(self, _func: Callable) -> Err[E]:
        return self

    async def then_async(self, _func: Callable) -> Err[E]:
        return self

    def __eq__(self, other) -> bool:
        return isinstance(other, Err) and self._error == other._error

    def __ne__(self, other) -> bool:
        return not (self == other)

    def __hash__(self) -> int:
        return hash((False, self._error))

    def __repr__(self) -> str:
        return f'Err({self._error!r})'


Option: TypeAlias = Union[Something[T], Nothing]
Result: TypeAlias = Union[Ok[T], Err[E]]


class UnwrapError(Exception):

    def __init__(self, result: Result, message: str):
        self._result = result
        super().__init__(message)


def call_as_result(*exceptions: Type[EX], func: Callable[..., R]) -> Result[R, EX]:
    return as_result(*exceptions)(func)()


async def call_as_async_result(*exceptions: Type[EX], func: Callable[..., Awaitable[R]]) -> Result[R, EX]:
    return await as_async_result(*exceptions)(func)()


def as_result(*exceptions: Type[EX]) -> Callable[[Callable[..., R]], Callable[..., Result]]:

    def decorator(func: Callable[..., R]) -> Callable[..., Result]:

        def wrapper(*args, **kwargs) -> Result[R, EX]:
            try:
                return Ok(func(*args, **kwargs))
            except exceptions or Exception as e:
                return Err(e)
            except UnwrapError as e:
                return Err(e)

        return wrapper

    return decorator


def as_async_result(*exceptions: Type[EX]) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[Result]]]:

    def decorator(func: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[Result]]:

        async def async_wrapper(*args, **kwargs) -> Result[R, EX]:
            try:
                return Ok(await func(*args, **kwargs))
            except exceptions or Exception as e:
                return Err(e)

        return async_wrapper

    return decorator


async def to_async(iterable: Iterable[T]) -> AsyncGenerator[T, None]:
    for item in iterable:
        await asyncio.sleep(0)
        yield item


class Pipeline(Generic[T]):

    def __init__(self, iterable: Iterable[T]):
        self._iterator = iter(iterable)

    def __iter__(self):
        return self._iterator

    def map(self, func) -> 'Pipeline[U]':
        return Pipeline(
            map(func, self._iterator)
        )

    def filter(self, func) -> 'Pipeline[U]':
        return Pipeline(
            filter(func, self._iterator)
        )

    def filter_map(self, func) -> 'Pipeline[U]':
        return Pipeline(
            filter(bool, map(func, self._iterator))
        )

    def collect_as(self, class_: CollectionType) -> Option[Collection]:
        collection = class_(self._iterator)
        if collection and len(collection):
            return Something(collection)

        return Nothing()

    async def to_async(self) -> AsyncGenerator[T, None]:
        return to_async(self._iterator)


class AsyncPipeline(Generic[T]):

    def __init__(self, generator: AsyncGenerator[T, None]):
        self._generator = generator

    def map(self, func: Union[Callable[[T], U], Callable[[T], Awaitable[U]]]) -> AsyncPipeline[U]:
        async def gen() -> AsyncGenerator[U, None]:
            async for item in self._generator:
                if inspect.iscoroutinefunction(func):
                    coro = cast(Callable[[T], Awaitable[U]], func)
                    yield await coro(item)
                else:
                    sync = cast(Callable[[T], U], func)
                    yield sync(item)

        return AsyncPipeline(gen())

    def filter(self, func: Union[Callable[[T], bool], Callable[[T], Awaitable[bool]]]) -> AsyncPipeline[T]:
        async def gen() -> AsyncGenerator[T, None]:
            async for item in self._generator:
                if inspect.iscoroutinefunction(func):
                    coro = cast(Callable[[T], Awaitable[bool]], func)
                    if await coro(item): yield item
                else:
                    sync = cast(Callable[[T], bool], func)
                    if sync(item): yield item

        return AsyncPipeline(gen())

    def filter_map(self, func: Union[Callable[[T], U], Callable[[T], Awaitable[U]]]) -> AsyncPipeline[U]:
        async def gen() -> AsyncGenerator[U, None]:
            async for item in self._generator:
                if inspect.iscoroutinefunction(func):
                    coro = cast(Callable[[T], Awaitable[U]], func)
                    if new := await coro(item): yield new
                else:
                    sync = cast(Callable[[T], U], func)
                    if new := sync(item): yield new

        return AsyncPipeline(gen())

    async def collect_as(self, class_: CollectionType) -> Option[Collection[T]]:
        collection = class_([item async for item in self._generator])
        if collection and len(collection):
            return Something(collection)

        return Nothing()

    @as_async_result(UnwrapError)
    async def unwrap_as(self: AsyncPipeline[Result[T, E]], class_: CollectionType) -> Collection[T]:
        item: Result[T, E]
        return class_([item.unwrap() async for item in self._generator])


def is_ok(res: Result[T, E]) -> TypeGuard[Ok[T]]: return res.is_ok()
def is_err(res: Result[T, E]) -> TypeGuard[Err[E]]: return res.is_err()
def is_something(opt: Option[T]) -> TypeGuard[T]: return opt.is_something()
def is_nothing(opt: Option[T]) -> TypeGuard[Nothing]: return opt.is_nothing()
