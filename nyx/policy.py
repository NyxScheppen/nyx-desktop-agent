"""Load system policy data without embedding fallback values in consumers.

JSON files under ``nyx/settings`` are the authoritative defaults. Runtime
configuration may override the public dataclass fields in ``nyx.config`` only.
Each lookup validates and reconstructs its declared Python type; mutable values
are copied so a caller cannot mutate the cached source for another character.
"""

import json
import math
import re
import types
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar, Union, cast, get_args, get_origin, overload

_T = TypeVar("_T")


class PolicyError(ValueError):
    """A required policy file, key, or value is invalid."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"重复策略键: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise PolicyError(f"策略不能包含非有限数字: {value}")


@lru_cache(maxsize=None)
def _document(system: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z_]*", system):
        raise PolicyError(f"非法系统名: {system!r}")
    path = Path(__file__).parent / "settings" / f"{system}.json"
    try:
        raw: Any = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise PolicyError(f"策略加载失败 {path}: {error}") from error
    if not isinstance(raw, dict):
        raise PolicyError(f"策略必须是对象: {path}")
    return cast(dict[str, Any], raw)


def _lookup(system: str, key: str, seen: frozenset[str] = frozenset()) -> Any:
    location = f"{system}:{key}"
    if location in seen:
        raise PolicyError(f"策略引用循环: {location}")
    value: Any = _document(system)
    for part in key.split("."):
        if isinstance(value, list) and part.isdecimal():
            items = cast(list[Any], value)
            if int(part) >= len(items):
                raise PolicyError(f"缺少策略: {location}")
            value = items[int(part)]
        elif isinstance(value, dict) and part in value:
            value = cast(dict[str, Any], value)[part]
        else:
            raise PolicyError(f"缺少策略: {location}")
    if isinstance(value, dict) and set(cast(dict[str, Any], value)) == {"$ref"}:
        reference: Any = cast(dict[str, Any], value)["$ref"]
        if not isinstance(reference, str) or ":" not in reference:
            raise PolicyError(f"非法策略引用: {location}")
        target, path = reference.split(":", 1)
        return _lookup(target, path, seen | {location})
    return cast(Any, value)


def _decode(value: Any, expected: Any, location: str) -> Any:
    origin, args = get_origin(expected), get_args(expected)
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _decode(value, option, location)
            except PolicyError:
                pass
    elif expected is type(None) and value is None:
        return None
    elif expected is bool and type(value) is bool:
        return value
    elif expected is int and type(value) is int:
        return value
    elif expected is float and type(value) in (int, float) and math.isfinite(value):
        return float(value)
    elif expected is str and isinstance(value, str):
        return value
    elif expected is Path and isinstance(value, str):
        return Path(value)
    elif origin is re.Pattern and isinstance(value, str):
        try:
            return re.compile(value)
        except re.error as error:
            raise PolicyError(f"非法正则 {location}: {error}") from error
    elif isinstance(expected, type) and issubclass(expected, Enum):
        try:
            return expected(value)
        except (ValueError, TypeError) as error:
            raise PolicyError(f"非法枚举 {location}: {value!r}") from error
    elif origin is dict and isinstance(value, dict):
        return {
            _decode(key, args[0], location): _decode(item, args[1], location)
            for key, item in cast(dict[str, Any], value).items()
        }
    elif origin in (list, tuple, set, frozenset) and isinstance(value, list):
        items = cast(list[Any], value)
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            if len(args) != len(items):
                raise PolicyError(f"策略元组长度错误: {location}")
            return tuple(
                _decode(item, kind, location) for item, kind in zip(items, args)
            )
        if not args:
            raise PolicyError(f"策略容器缺少元素类型: {location}")
        factory: Any = origin
        element_type: Any = args[0]
        return factory(_decode(item, element_type, location) for item in items)
    raise PolicyError(f"策略类型错误 {location}: 需要 {expected}, 得到 {value!r}")


@overload
def policy(system: str, key: str, expected: type[_T]) -> _T: ...


@overload
def policy(system: str, key: str, expected: object) -> Any: ...


def policy(system: str, key: str, expected: object) -> Any:
    """Read a required typed value; missing data never falls back to code."""
    return _decode(_lookup(system, key), expected, f"{system}:{key}")


def validate_policy_catalog() -> None:
    """Validate every file and reference, including defaults not yet consumed."""

    def visit(system: str, value: Any, key: str) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if set(mapping) == {"$ref"}:
                _lookup(system, key)
            else:
                for name, item in mapping.items():
                    visit(system, item, f"{key}.{name}" if key else name)
        elif isinstance(value, list):
            for index, item in enumerate(cast(list[Any], value)):
                visit(system, item, f"{key}.{index}")

    for path in (Path(__file__).parent / "settings").glob("*.json"):
        visit(path.stem, _document(path.stem), "")
