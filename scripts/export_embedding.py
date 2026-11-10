"""Export an offline FP32 artifact, including the original pooling/normalization.

Run with the embedding-export extra. Runtime never imports these dependencies.
"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

from nyx.memory.embedding import model_directory


def export_model(model_name: str, output: Path) -> Path:
    """Publish a complete validated artifact; interrupted exports stay invisible."""
    if output.exists():
        raise FileExistsError(f"Export destination already exists: {output}")
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    model = SentenceTransformer(
        model_name, device="cpu", model_kwargs={"attn_implementation": "eager"}
    )
    model.float().eval()
    if model.max_seq_length != 512:
        raise ValueError("This export requires a model with a 512-token limit")
    tokenizer: Any = model.tokenizer
    inputs = tokenizer(["中文记忆导出验证"], return_tensors="pt")
    input_names = [name for name in tokenizer.model_input_names if name in inputs]

    class SentenceModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.model = model

        def forward(self, *values: torch.Tensor) -> torch.Tensor:
            features = dict(zip(input_names, values))
            return cast(torch.Tensor, self.model(features)["sentence_embedding"])

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".embedding-", dir=output.parent) as temp:
        stage = Path(temp) / "model"
        stage.mkdir()
        graph = stage / "model.onnx"
        export = getattr(torch.onnx, "export")
        export(
            SentenceModel(),
            tuple(inputs[name] for name in input_names),
            str(graph),
            input_names=input_names,
            output_names=["sentence_embedding"],
            dynamic_axes={
                **{name: {0: "batch", 1: "sequence"} for name in input_names},
                "sentence_embedding": {0: "batch"},
            },
            opset_version=17,
            dynamo=False,
            external_data=False,
        )
        tokenizer.backend_tokenizer.save(str(stage / "tokenizer.json"))
        metadata = {
            "source": model_name,
            "revision": getattr(
                cast(Any, model[0]).auto_model.config, "_commit_hash", None
            ),
            "precision": "float32",
            "max_length": 512,
            "do_lower_case": bool(getattr(model[0], "do_lower_case", False)),
            "dimension": model.get_embedding_dimension(),
        }
        (stage / "embedding.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Validate the complete runtime path, including Rust tokenizer parity.
        from nyx.memory.embedding import OnnxEncoder

        encoder = OnnxEncoder(str(stage))
        cases = [
            "我喜欢和你一起读书。",
            "  Python、FastAPI 与 SQLite，今天学习了 ONNX。  ",
            "记忆需要保持连续，不能忘记前面的约定。" * 80,
            "",
            "词表包含 [PAD]、[UNK]、[CLS]、[SEP]、[MASK] 和 Python 标记。",
        ]
        encode = getattr(model, "encode")
        reference = encode(cases, show_progress_bar=False)
        actual = np.asarray([encoder(text) for text in cases], dtype=np.float32)
        error = float(np.max(np.abs(reference - actual)))
        if error > 1e-5:
            raise ValueError(f"ONNX export/tokenizer parity failed: max error {error}")
        del encoder
        # Release the session's Windows file handles before moving the artifact.
        shutil.move(str(stage), str(output))
    print(f"FP32 ONNX model: {output} (max absolute error {error:.3g})")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or model_directory(args.model)
    if args.output is None and (output / "embedding.json").is_file():
        print(f"Using existing FP32 ONNX model: {output}")
        return
    export_model(args.model, output)


if __name__ == "__main__":
    main()
