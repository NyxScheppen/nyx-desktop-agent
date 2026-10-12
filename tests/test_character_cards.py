import importlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from nyx.db import connect
from nyx.desire.store import DesireStore
from nyx.inner_life.store import InnerLifeStore


def package(extra: dict[str, bytes] | None = None) -> bytes:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "id": "alice",
        "name": "Alice",
        "personality": dict.fromkeys(
            [
                "openness",
                "conscientiousness",
                "extraversion",
                "agreeableness",
                "neuroticism",
            ],
            5,
        ),
        "values": dict.fromkeys(
            [
                "attitude_to_human",
                "ai_identity_acceptance",
                "altruism",
                "optimism",
            ],
            5,
        ),
        "aesthetic": dict.fromkeys(["ornate", "lyrical", "classical", "somber"], 5),
        "identity": "Alice",
        "long_term_desires": [],
    }
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("card.yaml", yaml.safe_dump(manifest))
        archive.writestr("canon.md", "你是 Alice。")
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return data.getvalue()


def test_card_import_rejects_escape_before_install(tmp_path: Path) -> None:
    cards = importlib.import_module("nyx.character_cards")
    with pytest.raises(ValueError, match="路径"):
        cards.read_package(package({"../escaped.md": b"bad"}))
    assert not (tmp_path / "escaped.md").exists()


def test_card_without_images_and_empty_desires_is_valid() -> None:
    cards = importlib.import_module("nyx.character_cards")
    card = cards.read_package(package())
    assert card.manifest.id == "alice"
    assert card.manifest.assets == {}
    assert card.manifest.long_term_desires == []


@pytest.mark.parametrize(
    "path", ["assets/CON.png", "assets/a?b.png", "assets/aux/x.png"]
)
def test_card_rejects_windows_reserved_paths(path: str) -> None:
    cards = importlib.import_module("nyx.character_cards")
    with pytest.raises(ValueError, match="路径"):
        cards.read_package(package({path: b"bad"}))


def test_card_rejects_file_directory_collision() -> None:
    cards = importlib.import_module("nyx.character_cards")
    with pytest.raises(ValueError, match="路径"):
        cards.read_package(
            package({"assets/picture": b"file", "assets/picture/x": b"x"})
        )


async def test_legacy_seed_does_not_resurrect_deleted_desires() -> None:
    bootstrap = importlib.import_module("nyx.bootstrap")
    db = await connect(":memory:")
    try:
        store = DesireStore(db)
        await bootstrap.seed_desire(store)
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM long_term_desire")
        await bootstrap.seed_desire(store)
        assert await store.list_long_term() == []
    finally:
        await db.close()


async def test_initialized_desires_stay_empty_after_restore() -> None:
    cards = importlib.import_module("nyx.character_cards")
    bootstrap = importlib.import_module("nyx.bootstrap")
    db = await connect(":memory:")
    try:
        card = cards.load_card(cards.BUILTIN_CARD)
        await bootstrap.initialize_card(db, card.manifest)
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM long_term_desire")
        await bootstrap.initialize_card(db, card.manifest)
        assert await DesireStore(db).list_long_term() == []
        personality = await InnerLifeStore(db).get_personality()
        assert personality is not None and personality["openness"] == 8
    finally:
        await db.close()


@pytest.mark.parametrize("invalid", ["unknown", "oversize"])
def test_installed_card_is_validated_like_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    from nyx import character_cards as cards

    with zipfile.ZipFile(io.BytesIO(package())) as archive:
        archive.extractall(tmp_path)
    if invalid == "unknown":
        (tmp_path / "script.py").write_text("pass", encoding="utf-8")
    else:
        monkeypatch.setattr(cards, "MAX_CARD_BYTES", 10)
    with pytest.raises(ValueError):
        cards.load_card(tmp_path)
