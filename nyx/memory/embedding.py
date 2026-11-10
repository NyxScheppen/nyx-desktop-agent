"""Lazy, bounded CPU embedding inference; export tools stay out of the runtime."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

_MAX_PENDING = 32
EmbedFn = Callable[[str], Awaitable[list[float]]]


def model_directory(model_name: str) -> Path:
    """Resolve an exported local model or its dedicated Hug Face cache directory."""
    local = Path(model_name)
    if local.is_dir():
        return local
    from huggingface_hub.constants import HF_HUB_CACHE

    name = model_name.replace("/", "--").replace("\\", "--")
    return Path(HF_HUB_CACHE) / f"models--{name}" / "nyx-onnx-fp32"


class OnnxEncoder:
    def __init__(self, model_name: str) -> None:
        import numpy as np
        import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]
        from tokenizers import Tokenizer

        directory = model_directory(model_name)
        if not (directory / "embedding.json").is_file():
            raise RuntimeError(
                f"Missing FP32 ONNX embedding model at {directory}. Run "
                "uv run --extra embedding-export python -m scripts.export_embedding "
                f'--model "{model_name}" first, or configure an exported directory.'
            )
        metadata = json.loads((directory / "embedding.json").read_text("utf-8"))
        if metadata["precision"] != "float32" or metadata["max_length"] != 512:
            raise ValueError("Embedding artifact must use FP32 and 512 tokens")
        self._dimension = int(metadata["dimension"])
        tokenizer_type = cast(Any, Tokenizer)
        self._tokenizer: Any = tokenizer_type.from_file(
            str(directory / "tokenizer.json")
        )
        self._tokenizer.enable_truncation(max_length=512)
        runtime = cast(Any, ort)  # C extension does not ship complete type stubs.
        options = runtime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
        self._session: Any = runtime.InferenceSession(
            str(directory / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._inputs: set[str] = {item.name for item in self._session.get_inputs()}
        self._np = np

    def __call__(self, text: str) -> list[float]:
        np = self._np
        # The exported tokenizer owns normalization. Lowercasing the raw string
        # here would turn literal [CLS]/[PAD]/[MASK] into ordinary text tokens.
        tokens = self._tokenizer.encode(text)
        values = {
            "input_ids": tokens.ids,
            "attention_mask": tokens.attention_mask,
            "token_type_ids": tokens.type_ids,
        }
        inputs = {
            name: np.asarray([values[name]], dtype=np.int64) for name in self._inputs
        }
        output = np.asarray(
            self._session.run(["sentence_embedding"], inputs)[0], dtype=np.float32
        )
        if output.shape != (1, self._dimension) or not np.isfinite(output).all():
            raise ValueError("Invalid ONNX sentence embedding")
        return cast(list[float], output[0].tolist())


def build_embed(model_name: str) -> EmbedFn:
    """Serialize actual inference, including work whose caller has been cancelled.

    Waiting callers are bounded. Cancellation removes queued work; an already
    running thread retains its slot until it really finishes, without swallowing
    the caller's cancellation. Failed loads are retried by the next request.
    """
    encoder: OnnxEncoder | None = None
    gate = asyncio.Semaphore(1)
    pending = 0

    def encode(text: str) -> list[float]:
        nonlocal encoder
        if encoder is None:
            encoder = OnnxEncoder(model_name)
        return encoder(text)

    def finished(task: asyncio.Task[list[float]]) -> None:
        nonlocal pending
        pending -= 1
        gate.release()
        if not task.cancelled():
            task.exception()  # Retrieve failures even if the caller was cancelled.

    async def embed(text: str) -> list[float]:
        nonlocal pending
        if pending >= _MAX_PENDING:
            raise RuntimeError("Embedding queue is full; retry later")
        pending += 1
        try:
            await gate.acquire()
        except BaseException:
            pending -= 1
            raise
        task = asyncio.create_task(asyncio.to_thread(encode, text))
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    # Hash artifacts once at composition, never on retrieval. Same dimensions do
    # not imply compatible models/tokenizers. Missing exports still fail lazily.
    digest = hashlib.sha256(model_name.encode())
    directory = model_directory(model_name)
    for name in ("embedding.json", "tokenizer.json", "model.onnx"):
        path = directory / name
        digest.update(name.encode())
        if path.is_file():
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
        else:
            digest.update(b"missing")
    setattr(embed, "index_version", digest.hexdigest())
    return embed
