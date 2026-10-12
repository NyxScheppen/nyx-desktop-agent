"""Native character card data and bounded, non-executable package validation."""

import io
import math
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from nyx.enums import DesireType

MAX_CARD_BYTES = 50 * 1024 * 1024
BUILTIN_CARD = Path(__file__).parent / "cards" / "nyx"
_DIMENSIONS = {
    "personality": {
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    },
    "values": {"attitude_to_human", "ai_identity_acceptance", "altruism", "optimism"},
    "aesthetic": {"ornate", "lyrical", "classical", "somber"},
}
_ASSET_KEYS = frozenset(
    {
        "avatar",
        "neutral",
        "happy",
        "sad",
        "angry",
        "worried",
        "shy",
        "sleepy",
        "thinking",
    }
)


class InitialDesire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: DesireType
    name: str = Field(min_length=1, max_length=200, strict=True)
    description: str = Field(min_length=1, max_length=6000, strict=True)
    strength: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    subtopics: list[str] = Field(default_factory=list)
    linked_values: list[str] = Field(default_factory=list)

    @field_validator("name", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("欲望文本不能为空")
        return value.strip()

    @field_validator("linked_values")
    @classmethod
    def valid_links(cls, value: list[str]) -> list[str]:
        if any(key not in _DIMENSIONS["values"] for key in value):
            raise ValueError("未知三观关联键")
        return list(dict.fromkeys(value))


class CardManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(strict=True)
    id: str = Field(strict=True)
    name: str = Field(min_length=1, max_length=100, strict=True)
    personality: dict[str, float]
    values: dict[str, float]
    aesthetic: dict[str, float]
    identity: str = Field(min_length=1, max_length=6000, strict=True)
    long_term_desires: list[InitialDesire] = Field(
        default_factory=lambda: list[InitialDesire](),
        max_length=5,
    )
    assets: dict[str, str] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("不支持的角色卡版本")
        return value

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        reserved = {"con", "prn", "aux", "nul", "host"}
        reserved.update(
            f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)
        )
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) or value in reserved:
            raise ValueError("非法角色 ID")
        return value

    @field_validator("name", "identity")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("角色文本不能为空")
        return value.strip()

    @field_validator("personality", "values", "aesthetic", mode="before")
    @classmethod
    def dimensions(cls, value: Any, info: ValidationInfo) -> dict[str, float]:
        if not isinstance(value, dict):
            raise ValueError("人格维度不完整")
        dimensions = cast(dict[str, Any], value)
        if set(dimensions) != _DIMENSIONS[info.field_name or ""]:
            raise ValueError("人格维度不完整")
        for number in dimensions.values():
            if (
                type(number) not in (int, float)
                or not math.isfinite(number)
                or not 1 <= number <= 10
            ):
                raise ValueError("人格数值必须是有限的 1–10")
        return dimensions

    @field_validator("long_term_desires")
    @classmethod
    def unique_desires(cls, value: list[InitialDesire]) -> list[InitialDesire]:
        names = [" ".join(item.name.casefold().split()) for item in value]
        if len(set(names)) != len(names):
            raise ValueError("重复长期欲望")
        return value

    @field_validator("assets")
    @classmethod
    def asset_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for key, path in value.items():
            if key not in _ASSET_KEYS or not safe_path(path).startswith("assets/"):
                raise ValueError("非法图片路径或表情键")
        return value


def safe_path(name: str) -> str:
    """Validate the same portable path on Windows and Unix before any writes."""
    path = PurePosixPath(name)
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10))
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(part.endswith((".", " ")) for part in path.parts)
        or any(char in '<>"|?*' or ord(char) < 32 for char in name)
        or any(part.split(".")[0].casefold() in reserved for part in path.parts)
    ):
        raise ValueError("卡包路径非法")
    if str(path) != name:
        raise ValueError("卡包路径不规范")
    return name


@dataclass(frozen=True)
class CharacterCard:
    manifest: CardManifest
    files: dict[str, bytes]

    @property
    def canon(self) -> str:
        return "\n\n".join(
            self.files[name].decode("utf-8")
            for name in ("canon.md", "character-reference.md")
            if name in self.files
        )

    @property
    def ask(self) -> str:
        return self.files.get("ask.md", b"").decode("utf-8")


def _validate(files: dict[str, bytes]) -> CharacterCard:
    if "card.yaml" not in files or not files.get("canon.md", b"").strip():
        raise ValueError("缺少 card.yaml 或非空 canon.md")
    try:
        manifest = CardManifest.model_validate(yaml.safe_load(files["card.yaml"]))
        card = CharacterCard(manifest, files)
        _ = card.canon, card.ask
        for path in manifest.assets.values():
            if path not in files:
                raise ValueError("图片文件缺失")
            with Image.open(io.BytesIO(files[path])) as picture:
                picture.verify()
            with Image.open(io.BytesIO(files[path])) as picture:
                picture.load()
    except (
        yaml.YAMLError,
        UnicodeError,
        OSError,
        Image.DecompressionBombError,
    ) as error:
        raise ValueError("角色卡内容无效") from error
    return card


def read_package(data: bytes) -> CharacterCard:
    """Read and validate an archive entirely before reserving a role slot."""
    if len(data) > MAX_CARD_BYTES:
        raise ValueError("卡包超过 50 MiB")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    regular: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                name = safe_path(
                    entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                )
                if name.casefold() in seen:
                    raise ValueError("重复卡包路径")
                normalized = name.casefold()
                if any(
                    parent.as_posix() in regular
                    for parent in PurePosixPath(normalized).parents
                ):
                    raise ValueError("卡包文件与目录路径冲突")
                if not entry.is_dir() and any(
                    p.startswith(normalized + "/") for p in seen
                ):
                    raise ValueError("卡包文件与目录路径冲突")
                seen.add(normalized)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("卡包路径不能是链接")
                if entry.is_dir():
                    continue
                regular.add(normalized)
                if name not in {
                    "card.yaml",
                    "canon.md",
                    "character-reference.md",
                    "ask.md",
                } and not name.startswith("assets/"):
                    raise ValueError("未知卡包文件")
                if entry.file_size > MAX_CARD_BYTES - total:
                    raise ValueError("解压数据超过 50 MiB")
                with archive.open(entry) as stream:
                    payload = stream.read(MAX_CARD_BYTES - total + 1)
                total += len(payload)
                if total > MAX_CARD_BYTES:
                    raise ValueError("解压数据超过 50 MiB")
                files[name] = payload
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError("无效角色卡压缩包") from error
    return _validate(files)


def load_card(directory: Path) -> CharacterCard:
    """Read a fixed installed snapshot (or the packaged built-in Nyx card)."""
    files: dict[str, bytes] = {}
    total = 0
    for path in directory.rglob("*"):
        name = safe_path(path.relative_to(directory).as_posix())
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("卡包路径不能是链接")
        if path.is_dir():
            continue
        if name not in {
            "card.yaml",
            "canon.md",
            "character-reference.md",
            "ask.md",
        } and not name.startswith("assets/"):
            raise ValueError("未知卡包文件")
        with path.open("rb") as stream:
            payload = stream.read(MAX_CARD_BYTES - total + 1)
        total += len(payload)
        if total > MAX_CARD_BYTES:
            raise ValueError("卡包超过 50 MiB")
        files[name] = payload
    return _validate(files)
