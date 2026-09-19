from __future__ import annotations

from dataclasses import dataclass

from nyx.enums import GameProfile
from nyx.types import GameChoice, GameTextBlock


@dataclass(frozen=True)
class NormalizedRegion:
    x1: float
    y1: float
    x2: float
    y2: float


PROFILE_VERSIONS: dict[GameProfile, int] = {
    GameProfile.DISCO_ELYSIUM: 1,
    GameProfile.REIGNS: 1,
    GameProfile.GENERIC_TEXT: 1,
}

PROFILE_MIN_SIZE: dict[GameProfile, tuple[int, int]] = {
    GameProfile.DISCO_ELYSIUM: (960, 540),
    GameProfile.REIGNS: (800, 450),
    GameProfile.GENERIC_TEXT: (1, 1),
}

PROFILE_REGIONS: dict[GameProfile, dict[str, NormalizedRegion]] = {
    GameProfile.DISCO_ELYSIUM: {
        "speaker_hint": NormalizedRegion(0.05, 0.42, 0.95, 0.62),
        "dialogue": NormalizedRegion(0.05, 0.52, 0.95, 0.86),
        "choice_candidates": NormalizedRegion(0.05, 0.58, 0.95, 0.98),
        "scene": NormalizedRegion(0.00, 0.00, 1.00, 0.60),
    },
    GameProfile.REIGNS: {
        "card": NormalizedRegion(0.16, 0.08, 0.84, 0.86),
        "left_action": NormalizedRegion(0.00, 0.20, 0.30, 0.82),
        "right_action": NormalizedRegion(0.70, 0.20, 1.00, 0.82),
        "resources": NormalizedRegion(0.00, 0.00, 1.00, 0.20),
    },
    GameProfile.GENERIC_TEXT: {},
}


def profile_version(profile: GameProfile) -> int:
    return PROFILE_VERSIONS[profile]


def regions_for(profile: GameProfile) -> dict[str, NormalizedRegion]:
    return PROFILE_REGIONS[profile]


def parse_choices(
    profile: GameProfile, blocks: list[GameTextBlock]
) -> list[GameChoice]:
    """Convert ordered OCR blocks to conservative choice candidates."""
    if profile is GameProfile.GENERIC_TEXT:
        return []
    ordered = sorted(blocks, key=lambda item: (item.bbox[1], item.bbox[0], item.id))
    return [
        GameChoice(
            id=f"choice:{index}",
            text=block.text.strip(),
            order=index,
            bbox=block.bbox,
            confidence=block.confidence,
            evidence_ids=list(block.evidence_ids),
        )
        for index, block in enumerate(ordered)
        if block.text.strip()
    ]
