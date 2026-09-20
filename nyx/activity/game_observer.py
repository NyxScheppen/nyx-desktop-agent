from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any, Protocol, cast

from nyx.activity.game_profiles import parse_choices
from nyx.enums import (
    EvidenceSource,
    GamePhase,
    GameProfile,
    ObservationStatus,
    TextSource,
)
from nyx.types import (
    AcceptedObservationSnapshot,
    GameChoice,
    GameObservation,
    GameTextBlock,
    GameVisionRequest,
    ObservationEvidence,
    ValidationReport,
)

_WHITESPACE = re.compile(r"\s+")
_MAX_OCR_BLOCKS = 128
_MAX_OCR_BLOCK_CHARS = 512
_MAX_OCR_BYTES = 16 * 1024
_MAX_CROPS = 4
_MAX_CROP_BYTES = 1024 * 1024
_MAX_TOTAL_CROP_BYTES = 3 * 1024 * 1024


class OcrEngine(Protocol):
    async def recognize(
        self, image_bytes: bytes, width: int, height: int
    ) -> tuple[list[GameTextBlock], str | None]: ...


class RapidOcrEngine:
    """Lazy RapidOCR adapter; model import/initialization stays off the UI path."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._engine: Callable[[Any], Any] | None = None

    async def recognize(
        self, image_bytes: bytes, width: int, height: int
    ) -> tuple[list[GameTextBlock], str | None]:
        try:
            blocks = await asyncio.wait_for(
                asyncio.to_thread(self._recognize_sync, image_bytes),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            return [], "ocr_unavailable"
        except (
            ImportError,
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
            AttributeError,
            IndexError,
            KeyError,
        ):
            return [], "ocr_unavailable"
        return blocks, None

    def _recognize_sync(self, image_bytes: bytes) -> list[GameTextBlock]:
        import importlib
        from io import BytesIO

        import numpy as np
        from PIL import Image

        if self._engine is None:
            module: Any = importlib.import_module("rapidocr_onnxruntime")
            factory = cast(
                Callable[[], Callable[[Any], Any]], module.RapidOCR
            )
            self._engine = factory()
        with Image.open(BytesIO(image_bytes)) as image:
            rgb = np.asarray(image.convert("RGB"))
            raw_result = self._engine(rgb)
            result = raw_result[0]
        blocks: list[GameTextBlock] = []
        for index, item in enumerate(cast(list[Any], result or [])):
            box, text, confidence = item
            points = [(int(point[0]), int(point[1])) for point in box]
            left = min(point[0] for point in points)
            top = min(point[1] for point in points)
            right = max(point[0] for point in points)
            bottom = max(point[1] for point in points)
            normalized = normalize_text(str(text))
            if not normalized:
                continue
            score = max(0.0, min(float(confidence), 1.0))
            blocks.append(
                GameTextBlock(
                    id=f"ocr:{index}",
                    text=normalized,
                    bbox=(left, top, right, bottom),
                    line_index=index,
                    confidence=score,
                    char_confidences=[score] * len(normalized),
                    source=TextSource.OCR,
                    evidence_ids=[],
                )
            )
        return blocks


def _bbox_in_bounds(
    bbox: tuple[int, int, int, int], width: int, height: int
) -> bool:
    left, top, right, bottom = bbox
    return 0 <= left < right <= width and 0 <= top < bottom <= height


def _text_similarity(
    left: list[GameTextBlock], right: list[GameTextBlock]
) -> float:
    a = "\n".join(normalize_text(item.text) for item in left)
    b = "\n".join(normalize_text(item.text) for item in right)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _evidence_for_block(block: GameTextBlock) -> ObservationEvidence:
    return ObservationEvidence(
        id=f"evidence:{block.id}",
        source=EvidenceSource.OCR,
        ref=block.id,
        bbox=block.bbox,
    )


def _with_evidence(block: GameTextBlock) -> GameTextBlock:
    return replace(block, evidence_ids=[f"evidence:{block.id}"])


def build_ocr_observation(
    *,
    session_id: str,
    game_id: str,
    profile: GameProfile,
    profile_version: int,
    threshold_version: int,
    revision: int,
    captured_at: float,
    width: int,
    height: int,
    blocks: list[GameTextBlock],
    previous: GameObservation | AcceptedObservationSnapshot | None = None,
    ocr_error: str | None = None,
) -> tuple[GameObservation, ValidationReport]:
    """Build a conservative image-free observation from injected OCR blocks.

    The function deliberately does not perform OCR or remote vision. It is the
    deterministic validation/fusion boundary used by those providers and by
    tests with fake OCR results.
    """
    hard_failures: list[str] = []
    for block in blocks:
        if not _bbox_in_bounds(block.bbox, width, height):
            hard_failures.append("bbox_out_of_bounds")
        if not 0.0 <= block.confidence <= 1.0:
            hard_failures.append("ocr_confidence_out_of_range")
        if any(not 0.0 <= value <= 1.0 for value in block.char_confidences):
            hard_failures.append("char_confidence_out_of_range")
        if not normalize_text(block.text):
            hard_failures.append("empty_ocr_text")

    normalized_blocks = [_with_evidence(block) for block in blocks]
    evidence = [_evidence_for_block(block) for block in blocks]
    dialogue: list[GameTextBlock] = []
    choice_blocks: list[GameTextBlock] = []
    for block in normalized_blocks:
        center_y = (block.bbox[1] + block.bbox[3]) / 2 / max(height, 1)
        if profile is GameProfile.GENERIC_TEXT:
            continue
        if center_y >= 0.58:
            choice_blocks.append(block)
        else:
            dialogue.append(block)
    choices = parse_choices(profile, choice_blocks)
    phase = phase_from_text(bool(choices), bool(dialogue))
    ocr_confidence = (
        sum(block.confidence for block in normalized_blocks) / len(normalized_blocks)
        if normalized_blocks else 0.0
    )
    evidence_coverage = 1.0 if normalized_blocks else 0.0
    temporal_agreement = 0.0
    if previous is not None:
        temporal_agreement = _text_similarity(
            normalized_blocks, previous.text_blocks
        )
    low_resolution = (
        (profile is GameProfile.DISCO_ELYSIUM and (width < 960 or height < 540))
        or (profile is GameProfile.REIGNS and (width < 800 or height < 450))
    )
    warnings = ["low_resolution"] if low_resolution else []
    if ocr_error is not None:
        warnings.append(ocr_error)
    score, components = score_observation(
        ocr_confidence, evidence_coverage, temporal_agreement, 0.5
    )
    stable = (
        previous is not None
        and captured_at - previous.captured_at <= 1.2
        and temporal_agreement >= 0.85
        and not low_resolution
    )
    report = build_validation_report(
        threshold_version=threshold_version,
        checked_revision=revision,
        score=score,
        score_components=components,
        hard_failures=sorted(set(hard_failures)),
        soft_warnings=warnings,
        stable=stable,
    )
    observation = GameObservation(
        session_id=session_id,
        game_id=game_id,
        profile=profile,
        profile_version=profile_version,
        threshold_version=threshold_version,
        phase=phase,
        status=report.status,
        speaker=None,
        speaker_evidence_ids=[],
        dialogue=dialogue,
        text_blocks=normalized_blocks,
        choices=choices,
        visible_entities=[],
        entity_evidence_ids=[],
        scene_summary=None,
        scene_evidence_ids=[],
        confidence=score,
        observation_hash="",
        captured_at=captured_at,
        revision=revision,
        evidence=evidence,
        uncertainties=[],
    )
    if observation.status is ObservationStatus.ACCEPTED:
        snapshot = _observation_snapshot(observation)
        observation = replace(
            observation, observation_hash=canonical_observation_hash(snapshot)
        )
    return observation, report


def _observation_snapshot(
    observation: GameObservation,
) -> AcceptedObservationSnapshot:
    return AcceptedObservationSnapshot(
        session_id=observation.session_id,
        game_id=observation.game_id,
        profile=observation.profile,
        profile_version=observation.profile_version,
        threshold_version=observation.threshold_version,
        revision=observation.revision,
        phase=observation.phase,
        observation_hash=observation.observation_hash,
        captured_at=observation.captured_at,
        speaker=observation.speaker,
        speaker_evidence_ids=observation.speaker_evidence_ids,
        dialogue=observation.dialogue,
        text_blocks=observation.text_blocks,
        choices=observation.choices,
        visible_entities=observation.visible_entities,
        entity_evidence_ids=observation.entity_evidence_ids,
        scene_summary=observation.scene_summary,
        scene_evidence_ids=observation.scene_evidence_ids,
        confidence=observation.confidence,
        evidence=observation.evidence,
        uncertainties=observation.uncertainties,
    )


def observation_to_snapshot(
    observation: GameObservation,
) -> AcceptedObservationSnapshot | None:
    """Return a durable snapshot only for accepted observations."""
    if observation.status is not ObservationStatus.ACCEPTED:
        return None
    snapshot = _observation_snapshot(observation)
    expected_hash = canonical_observation_hash(snapshot)
    if snapshot.observation_hash != expected_hash:
        snapshot = replace(snapshot, observation_hash=expected_hash)
    return snapshot


def normalize_text(value: str) -> str:
    """Apply the text normalization shared by hash and evidence checks."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()


def canonical_observation_hash(snapshot: AcceptedObservationSnapshot) -> str:
    """Hash only stable visible semantics, excluding provenance and timing."""
    payload: dict[str, Any] = {
        "choices": [normalize_text(item.text) for item in snapshot.choices],
        "dialogue": [normalize_text(item.text) for item in snapshot.dialogue],
        "game_id": normalize_text(snapshot.game_id),
        "phase": snapshot.phase.value,
        "profile": snapshot.profile.value,
        "profile_version": snapshot.profile_version,
        "scene_summary": (
            normalize_text(snapshot.scene_summary)
            if snapshot.scene_summary is not None else None
        ),
        "speaker": (
            normalize_text(snapshot.speaker) if snapshot.speaker is not None else None
        ),
        "text_blocks": [normalize_text(item.text) for item in snapshot.text_blocks],
        "threshold_version": snapshot.threshold_version,
        "visible_entities": sorted(
            (normalize_text(item) for item in snapshot.visible_entities),
            key=lambda item: (item.casefold(), item),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def score_observation(
    ocr_confidence: float,
    evidence_coverage: float,
    temporal_agreement: float,
    source_agreement: float,
) -> tuple[float, dict[str, float]]:
    values = {
        "ocr_confidence": ocr_confidence,
        "evidence_coverage": evidence_coverage,
        "temporal_agreement": temporal_agreement,
        "source_agreement": source_agreement,
    }
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("score 分量必须是有限数")
    if any(value < 0.0 or value > 1.0 for value in values.values()):
        raise ValueError("score 分量必须在 [0, 1]")
    score = (
        0.35 * ocr_confidence
        + 0.25 * evidence_coverage
        + 0.20 * temporal_agreement
        + 0.20 * source_agreement
    )
    return score, values


def validate_game_vision_request(request: GameVisionRequest) -> None:
    """Validate transient request bounds before constructing a remote prompt."""
    if len(request.crops) > _MAX_CROPS:
        raise ValueError("crop 数量超过上限")
    total_crops = 0
    for crop in request.crops:
        if crop.mime_type != "image/png" or len(crop.image_bytes) > _MAX_CROP_BYTES:
            raise ValueError("crop 大小或类型非法")
        total_crops += len(crop.image_bytes)
    if total_crops > _MAX_TOTAL_CROP_BYTES:
        raise ValueError("crop 总大小超过上限")
    if len(request.ocr_candidates) > _MAX_OCR_BLOCKS:
        raise ValueError("ocr block 数量超过上限")
    total_text_bytes = 0
    for block in request.ocr_candidates:
        if len(block.text) > _MAX_OCR_BLOCK_CHARS:
            raise ValueError("ocr block 文本超过上限")
        if len(block.char_confidences) > _MAX_OCR_BLOCK_CHARS:
            raise ValueError("ocr char confidence 超过上限")
        total_text_bytes += len(block.text.encode("utf-8"))
    if total_text_bytes > _MAX_OCR_BYTES:
        raise ValueError("ocr 文本总大小超过上限")
    if request.profile_version <= 0:
        raise ValueError("profile_version 非法")


def phase_from_text(has_choices: bool, has_dialogue: bool) -> GamePhase:
    """Small deterministic fallback used when no vision provider is available."""
    if has_choices:
        return GamePhase.CHOICE
    if has_dialogue:
        return GamePhase.DIALOGUE
    return GamePhase.UNKNOWN


def validate_evidence_references(
    observation: GameObservation | AcceptedObservationSnapshot,
) -> list[str]:
    """Return deterministic hard-failure codes for missing evidence references."""
    evidence = {item.id: item for item in observation.evidence}
    failures: list[str] = []
    for field, ids in (
        ("speaker", observation.speaker_evidence_ids),
        (
            "dialogue",
            [ref for item in observation.dialogue for ref in item.evidence_ids],
        ),
        (
            "choices",
            [ref for item in observation.choices for ref in item.evidence_ids],
        ),
        ("entities", observation.entity_evidence_ids),
        ("scene", observation.scene_evidence_ids),
    ):
        if any(ref not in evidence for ref in ids):
            failures.append(f"missing_evidence:{field}")
    if observation.speaker is not None and not observation.speaker_evidence_ids:
        failures.append("speaker_without_evidence")
    if observation.dialogue and not all(
        item.evidence_ids for item in observation.dialogue
    ):
        failures.append("dialogue_without_evidence")
    if observation.choices and not all(
        item.evidence_ids for item in observation.choices
    ):
        failures.append("choice_without_evidence")
    if observation.visible_entities and not observation.entity_evidence_ids:
        failures.append("entities_without_evidence")
    if observation.scene_summary is not None and not observation.scene_evidence_ids:
        failures.append("scene_without_evidence")
    return failures


def build_validation_report(
    *,
    threshold_version: int,
    checked_revision: int,
    score: float,
    score_components: dict[str, float],
    hard_failures: list[str],
    soft_warnings: list[str] | None = None,
    stable: bool = False,
) -> ValidationReport:
    if hard_failures:
        status = ObservationStatus.REJECTED
    elif score >= 0.80 and stable:
        status = ObservationStatus.ACCEPTED
    elif score >= 0.55:
        status = ObservationStatus.TENTATIVE
    else:
        status = ObservationStatus.REJECTED
    return ValidationReport(
        status,
        score,
        score_components,
        threshold_version,
        list(hard_failures),
        list(soft_warnings or []),
        [],
        checked_revision,
    )


def snapshot_from_dict(raw: dict[str, Any]) -> AcceptedObservationSnapshot:
    """Decode the image-free checkpoint representation at the facade boundary."""
    def block(value: dict[str, Any]) -> GameTextBlock:
        return GameTextBlock(
            str(value["id"]), str(value["text"]), tuple(value["bbox"]),
            int(value["line_index"]), float(value["confidence"]),
            [float(item) for item in value.get("char_confidences", [])],
            TextSource(value.get("source", TextSource.OCR.value)),
            [str(item) for item in value.get("evidence_ids", [])],
        )
    def choice(value: dict[str, Any]) -> Any:
        return GameChoice(
            str(value["id"]), str(value["text"]), int(value["order"]),
            tuple(value["bbox"]) if value.get("bbox") is not None else None,
            float(value["confidence"]),
            [str(item) for item in value.get("evidence_ids", [])],
        )
    evidence = [
        ObservationEvidence(
            str(item["id"]), EvidenceSource(item["source"]), str(item["ref"]),
            tuple(item["bbox"]) if item.get("bbox") is not None else None,
        )
        for item in raw.get("evidence", [])
    ]
    return AcceptedObservationSnapshot(
        str(raw["session_id"]), str(raw["game_id"]), GameProfile(raw["profile"]),
        int(raw["profile_version"]),
        int(raw["threshold_version"]),
        int(raw["revision"]),
        GamePhase(raw["phase"]),
        str(raw["observation_hash"]),
        float(raw["captured_at"]),
        raw.get("speaker"), [str(item) for item in raw.get("speaker_evidence_ids", [])],
        [block(item) for item in raw.get("dialogue", [])],
        [block(item) for item in raw.get("text_blocks", [])],
        [choice(item) for item in raw.get("choices", [])],
        [str(item) for item in raw.get("visible_entities", [])],
        [str(item) for item in raw.get("entity_evidence_ids", [])],
        raw.get("scene_summary"),
        [str(item) for item in raw.get("scene_evidence_ids", [])],
        float(raw["confidence"]),
        evidence,
        [str(item) for item in raw.get("uncertainties", [])],
    )


def snapshot_to_dict(snapshot: AcceptedObservationSnapshot) -> dict[str, Any]:
    """Encode a durable snapshot without carrying image bytes."""
    def block(item: GameTextBlock) -> dict[str, Any]:
        return {
            "id": item.id,
            "text": item.text,
            "bbox": list(item.bbox),
            "line_index": item.line_index,
            "confidence": item.confidence,
            "char_confidences": item.char_confidences,
            "source": item.source.value,
            "evidence_ids": item.evidence_ids,
        }
    def choice(item: GameChoice) -> dict[str, Any]:
        return {
            "id": item.id,
            "text": item.text,
            "order": item.order,
            "bbox": list(item.bbox) if item.bbox is not None else None,
            "confidence": item.confidence,
            "evidence_ids": item.evidence_ids,
        }
    return {
        "session_id": snapshot.session_id,
        "game_id": snapshot.game_id,
        "profile": snapshot.profile.value,
        "profile_version": snapshot.profile_version,
        "threshold_version": snapshot.threshold_version,
        "revision": snapshot.revision,
        "phase": snapshot.phase.value,
        "observation_hash": snapshot.observation_hash,
        "captured_at": snapshot.captured_at,
        "speaker": snapshot.speaker,
        "speaker_evidence_ids": snapshot.speaker_evidence_ids,
        "dialogue": [block(item) for item in snapshot.dialogue],
        "text_blocks": [block(item) for item in snapshot.text_blocks],
        "choices": [choice(item) for item in snapshot.choices],
        "visible_entities": snapshot.visible_entities,
        "entity_evidence_ids": snapshot.entity_evidence_ids,
        "scene_summary": snapshot.scene_summary,
        "scene_evidence_ids": snapshot.scene_evidence_ids,
        "confidence": snapshot.confidence,
        "evidence": [
            {
                "id": item.id,
                "source": item.source.value,
                "ref": item.ref,
                "bbox": list(item.bbox) if item.bbox is not None else None,
            }
            for item in snapshot.evidence
        ],
        "uncertainties": snapshot.uncertainties,
    }
