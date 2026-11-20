"""应用启动时的 prompt 加载与幂等 seed。"""

import json
import time
import uuid
from pathlib import Path
from typing import cast

from nyx.character_cards import BUILTIN_CARD, CardManifest, load_card
from nyx.db import Database
from nyx.desire.store import DesireStore
from nyx.desire.value import default_value
from nyx.enums import DesireType, EnergyState
from nyx.inner_life.store import InnerLifeStore
from nyx.policy import policy
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    InnerDocument,
    LongTermDesire,
    Skill,
)


async def initialize_card(db: Database, card: CardManifest) -> None:
    """Commit defaults and their marker together; never resurrect old ambitions."""
    inner = InnerLifeStore(db)
    desires = DesireStore(db)
    now = time.time()
    async with db.transaction() as conn:
        marker = await (
            await conn.execute(
                "SELECT 1 FROM character_initialization WHERE id = ?", (card.id,)
            )
        ).fetchone()
        if marker is not None:
            return
        existing = await (
            await conn.execute(
                "SELECT 1 FROM inner_document "
                "UNION ALL SELECT 1 FROM desire_value UNION ALL SELECT 1 FROM "
                "event_log "
                "UNION ALL SELECT 1 FROM long_term_desire LIMIT 1"
            )
        ).fetchone()
        if await inner.get_energy() is None:
            await inner.upsert_energy(
                policy("inner_life", "energy.maximum", float), EnergyState.ENERGETIC
            )
        if await inner.get_document() is None:
            await inner.upsert_document(
                InnerDocument(
                    topics=card.inner_document,
                    revision=1,
                    updated_at=now,
                )
            )
        for desire_type in DesireType:
            if await desires.get_value(desire_type) is None:
                value = default_value(desire_type)
                value.updated_at = now
                await desires.upsert_value(value)
        if existing is None:
            for seed in card.long_term_desires:
                await desires.insert_long_term(
                    LongTermDesire(
                        id=str(uuid.uuid4()),
                        created_at=now,
                        type=seed.type,
                        name=seed.name,
                        description=seed.description,
                        strength=seed.strength,
                        progress=0.0,
                        subtopics=seed.subtopics,
                    )
                )
        await conn.execute(
            "INSERT INTO character_initialization VALUES (?)", (card.id,)
        )


def load_prompt_files(canon_dir: Path, names: tuple[str, ...]) -> str:
    """Read and combine prompt files, failing fast when one is missing."""
    parts: list[str] = []
    for name in names:
        if (
            canon_dir.resolve() == (Path(__file__).parent / "prompts").resolve()
            and name == "knowledge-boundary.md"
        ):
            parts.append(policy("expression", "knowledge_boundary", str))
            continue
        path = canon_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                policy(
                    "character", "bootstrap.templates.load_prompt_files_text", str
                ).format(p0=path)
            )
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def load_canon(canon_dir: Path, names: tuple[str, ...]) -> str:
    """Load canon prompt files used to ground the assistant."""
    return load_prompt_files(canon_dir, names)


def load_ask(canon_dir: Path, names: tuple[str, ...]) -> str:
    """Load the guidance for proactive questions."""
    return load_prompt_files(canon_dir, names)


def load_skills(skills_dir: Path, registry: ToolRegistry) -> dict[str, Skill]:
    """Load declarative skills and reject invalid tool grants at startup."""
    sources: list[tuple[Path, object]]
    if skills_dir.resolve() == (Path(__file__).parent / "prompts" / "skills").resolve():
        defaults = policy("behavior", "skills", dict[str, dict[str, str | list[str]]])
        sources = [(skills_dir / f"{name}.json", raw) for name, raw in defaults.items()]
    else:
        if not skills_dir.is_dir():
            raise FileNotFoundError(
                policy("character", "bootstrap.templates.load_skills_text", str).format(
                    p0=skills_dir
                )
            )
        sources = [
            (path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(skills_dir.glob("*.json"))
        ]
    skills: dict[str, Skill] = {}
    for path, raw in sources:
        if not isinstance(raw, dict):
            raise ValueError(
                policy(
                    "character", "bootstrap.templates.load_skills_text_2", str
                ).format(p0=path)
            )
        data = cast(dict[str, object], raw)
        name = data.get("name")
        fragment = data.get("system_fragment")
        names = data.get("tool_names")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(fragment, str)
            or not fragment.strip()
            or len(fragment)
            > policy("character", "bootstrap.parameters.load_skills_value_limit", int)
            or not isinstance(names, list)
            or (not names and name != "conversation")
            or any(
                not isinstance(item, str) or not item
                for item in cast(list[object], names)
            )
        ):
            raise ValueError(
                policy(
                    "character", "bootstrap.templates.load_skills_text_3", str
                ).format(p0=path)
            )
        if name in skills:
            raise ValueError(
                policy(
                    "character", "bootstrap.templates.load_skills_text_4", str
                ).format(p0=name)
            )
        tool_names = tuple(cast(list[str], names))
        registry.schema(tool_names)
        skills[name] = Skill(name, fragment, tool_names)
    for required in policy("behavior", "required_skills", tuple[str, ...]):
        if required not in skills:
            raise ValueError(
                policy(
                    "character", "bootstrap.templates.load_skills_text_5", str
                ).format(p0=required, p1=skills_dir)
            )
    return skills


async def seed_inner_life(store: InnerLifeStore) -> None:
    """Initialize the legacy Nyx save from the same immutable card defaults."""
    await initialize_card(store.db, load_card(BUILTIN_CARD).manifest)


async def seed_desire(store: DesireStore) -> None:
    """Initialize once; an empty established ambition list stays empty."""
    await initialize_card(store.db, load_card(BUILTIN_CARD).manifest)
