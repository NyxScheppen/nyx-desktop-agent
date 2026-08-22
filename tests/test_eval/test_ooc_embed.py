from nyx.eval.ooc_embed import (
    NYX_CORPUS,
    build_baseline,
    is_voice_type,
    ooc_embed_score,
)


async def test_build_baseline_len() -> None:
    async def _embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    baseline = await build_baseline(_embed)
    assert len(baseline) == len(NYX_CORPUS)
    assert baseline[0] == [1.0, 0.0, 0.0]


async def test_ooc_embed_score_identical() -> None:
    async def _embed(text: str) -> list[float]:
        return [1.0, 0.0]

    # sim = 1.0 → 1.0/0.7 越界，clamp 到 1.0
    assert await ooc_embed_score(_embed, "x", [[1.0, 0.0]]) == 1.0


async def test_ooc_embed_score_orthogonal() -> None:
    async def _embed(text: str) -> list[float]:
        return [0.0, 1.0]

    # sim = 0.0 → 0.0
    assert await ooc_embed_score(_embed, "x", [[1.0, 0.0]]) == 0.0


async def test_ooc_embed_score_empty_baseline() -> None:
    async def _embed(text: str) -> list[float]:
        return [1.0, 0.0]

    # 无语料无信息 → 不惩罚（1.0）
    assert await ooc_embed_score(_embed, "x", []) == 1.0


def test_is_voice_type() -> None:
    assert is_voice_type("speak")
    assert is_voice_type("initiate_chat")
    assert is_voice_type("think")
    assert not is_voice_type("tool")
    assert not is_voice_type("judge")
    assert not is_voice_type("scene_memory")
