import builtins
from typing import Annotated

from typing_extensions import Self

from pydantic import BaseModel, ValidationError, BeforeValidator
from pydantic_core import PydanticSerializationError

from app.pipeline import as_result


PyObjectId = Annotated[str, BeforeValidator(str)]


class ResultModel(BaseModel):

    @classmethod
    @as_result(ValidationError, TypeError, ValueError)
    def from_(cls, obj: dict | str | bytes | bytearray, **kwargs) -> Self:
        match obj:
            case dict():
                adjusted = {**obj, "_id": obj["id"]} if "id" in obj else obj
                return cls.model_validate(adjusted, **kwargs)
            case str() | bytes() | bytearray(): return cls.model_validate_json(obj, **kwargs)
            case _: raise TypeError(f"Incompatible `obj` type for {cls.__name__}")

    @as_result(PydanticSerializationError, TypeError)
    def to_(self, type_: type[dict | str], by_alias=True, **kwargs) -> dict | str:
        match type_:
            case builtins.dict: return self.model_dump(by_alias=by_alias, **kwargs)
            case builtins.str: return self.model_dump_json(by_alias=by_alias, **kwargs)
            case _: raise TypeError(f"Incompatible return `type` for {self.__class__.__name__}")

    @as_result(ValidationError)
    def update(self, **fields) -> Self:
        return self.copy(update=fields)
