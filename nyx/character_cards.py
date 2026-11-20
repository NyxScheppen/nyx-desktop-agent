"""Native character card data and bounded, non-executable package validation."""

import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nyx.enums import DesireType
from nyx.inner_life.document import validate_topics
from nyx.policy import policy
from nyx.types import InnerTopic

MAX_CARD_BYTES = policy("character", "character_cards.MAX_CARD_BYTES", int)
BUILTIN_CARD = Path(__file__).parent / "cards" / "nyx"
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
    name: str = Field(
        min_length=1,
        max_length=policy("character", "limits.InitialDesire_name_max_length", int),
        strict=True,
    )
    description: str = Field(
        min_length=1,
        max_length=policy(
            "character", "limits.InitialDesire_description_max_length", int
        ),
        strict=True,
    )
    strength: float = Field(
        default=policy("character", "character_cards.default_desire_strength", float),
        ge=0,
        le=1,
        strict=True,
        allow_inf_nan=False,
    )
    subtopics: list[str] = Field(default_factory=list)

    @field_validator("name", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                policy("character", "character_cards.copy.nonblank_text", str)
            )
        return value.strip()


class CardManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(strict=True)
    id: str = Field(strict=True)
    name: str = Field(
        min_length=1,
        max_length=policy("character", "limits.CardManifest_name_max_length", int),
        strict=True,
    )
    inner_document: list[InnerTopic]
    long_term_desires: list[InitialDesire] = Field(
        default_factory=lambda: list[InitialDesire](),
        max_length=policy(
            "character", "limits.CardManifest_long_term_desires_max_length", int
        ),
    )
    assets: dict[str, str] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def version_two(cls, value: int) -> int:
        if value != 2:
            raise ValueError(
                policy("character", "character_cards.copy.version_two_text", str)
            )
        return value

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        reserved = {"con", "prn", "aux", "nul", "host"}
        reserved.update(
            f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)
        )
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) or value in reserved:
            raise ValueError(
                policy("character", "character_cards.copy.safe_id_text", str)
            )
        return value

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                policy("character", "character_cards.copy.nonblank_text_2", str)
            )
        return value.strip()

    @field_validator("inner_document")
    @classmethod
    def valid_document(cls, value: list[InnerTopic]) -> list[InnerTopic]:
        validate_topics(value)
        return value

    @field_validator("long_term_desires")
    @classmethod
    def unique_desires(cls, value: list[InitialDesire]) -> list[InitialDesire]:
        names = [" ".join(item.name.casefold().split()) for item in value]
        if len(set(names)) != len(names):
            raise ValueError(
                policy("character", "character_cards.copy.unique_desires_text", str)
            )
        return value

    @field_validator("assets")
    @classmethod
    def asset_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for key, path in value.items():
            if key not in _ASSET_KEYS or not safe_path(path).startswith("assets/"):
                raise ValueError(
                    policy("character", "character_cards.copy.asset_paths_text", str)
                )
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
        raise ValueError(
            policy("character", "character_cards.copy.safe_path_text", str)
        )
    if str(path) != name:
        raise ValueError(
            policy("character", "character_cards.copy.safe_path_text_2", str)
        )
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
        raise ValueError(policy("character", "character_cards.copy.validate_text", str))
    try:
        manifest = CardManifest.model_validate(yaml.safe_load(files["card.yaml"]))
        card = CharacterCard(manifest, files)
        _ = card.canon, card.ask
        for path in manifest.assets.values():
            if path not in files:
                raise ValueError(
                    policy("character", "character_cards.copy.validate_text_3", str)
                )
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
        raise ValueError(
            policy("character", "character_cards.copy.validate_text_2", str)
        ) from error
    return card


def read_package(data: bytes) -> CharacterCard:
    """Read and validate an archive entirely before reserving a role slot."""
    if len(data) > MAX_CARD_BYTES:
        raise ValueError(
            policy("character", "character_cards.copy.read_package_text", str)
        )
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
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.read_package_text_3", str
                        )
                    )
                normalized = name.casefold()
                if any(
                    parent.as_posix() in regular
                    for parent in PurePosixPath(normalized).parents
                ):
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.read_package_text_4", str
                        )
                    )
                if not entry.is_dir() and any(
                    p.startswith(normalized + "/") for p in seen
                ):
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.read_package_text_4", str
                        )
                    )
                seen.add(normalized)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(
                        policy("character", "character_cards.copy.load_card_text", str)
                    )
                if entry.is_dir():
                    continue
                regular.add(normalized)
                if name not in {
                    "card.yaml",
                    "canon.md",
                    "character-reference.md",
                    "ask.md",
                } and not name.startswith("assets/"):
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.load_card_text_2", str
                        )
                    )
                if entry.file_size > MAX_CARD_BYTES - total:
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.read_package_text_5", str
                        )
                    )
                with archive.open(entry) as stream:
                    payload = stream.read(MAX_CARD_BYTES - total + 1)
                total += len(payload)
                if total > MAX_CARD_BYTES:
                    raise ValueError(
                        policy(
                            "character", "character_cards.copy.read_package_text_5", str
                        )
                    )
                files[name] = payload
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError(
            policy("character", "character_cards.copy.read_package_text_2", str)
        ) from error
    return _validate(files)


def build_package(
    manifest: dict[str, Any], canon: str, assets: dict[str, bytes]
) -> bytes:
    """Build and validate a standard card archive from structured form data."""
    files: dict[str, bytes] = {
        "card.yaml": yaml.safe_dump(
            manifest, allow_unicode=True, sort_keys=False
        ).encode("utf-8"),
        "canon.md": canon.encode("utf-8"),
    }
    for key, payload in assets.items():
        files[f"assets/{key}"] = payload
    _validate(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def load_card(directory: Path) -> CharacterCard:
    """Read a fixed installed snapshot (or the packaged built-in Nyx card)."""
    files: dict[str, bytes] = {}
    total = 0
    for path in directory.rglob("*"):
        name = safe_path(path.relative_to(directory).as_posix())
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(
                policy("character", "character_cards.copy.load_card_text", str)
            )
        if path.is_dir():
            continue
        if name not in {
            "card.yaml",
            "canon.md",
            "character-reference.md",
            "ask.md",
        } and not name.startswith("assets/"):
            raise ValueError(
                policy("character", "character_cards.copy.load_card_text_2", str)
            )
        with path.open("rb") as stream:
            payload = stream.read(MAX_CARD_BYTES - total + 1)
        total += len(payload)
        if total > MAX_CARD_BYTES:
            raise ValueError(
                policy("character", "character_cards.copy.read_package_text", str)
            )
        files[name] = payload
    return _validate(files)
