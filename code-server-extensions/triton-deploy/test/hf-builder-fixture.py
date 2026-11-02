"""Create a tiny local checkpoint for generation tests; never run model inference."""

import json
import struct
import sys
from pathlib import Path


def create_checkpoint(destination: Path, task: str = "text-generation") -> None:
    architectures = {
        "text-generation": ("gpt2", "GPT2LMHeadModel"),
        "text-classification": ("bert", "BertForSequenceClassification"),
        "text2text-generation": ("t5", "T5ForConditionalGeneration"),
        "feature-extraction": ("bert", "BertModel"),
    }
    model_type, architecture = architectures[task]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "config.json").write_text(json.dumps({
        "model_type": model_type, "architectures": [architecture], "vocab_size": 4,
        "hidden_size": 4, "n_embd": 4, "n_layer": 1, "n_head": 1,
        "num_hidden_layers": 1, "num_attention_heads": 1, "num_labels": 2,
        "is_encoder_decoder": task == "text2text-generation",
    }), encoding="utf-8")
    (destination / "tokenizer.json").write_text(json.dumps({
        "version": "1.0", "model": {"type": "WordLevel", "vocab": {"[UNK]": 0, "test": 1},
                                     "unk_token": "[UNK]"},
    }), encoding="utf-8")
    (destination / "tokenizer_config.json").write_text('{"model_max_length": 32}', encoding="utf-8")
    # A valid safetensors container holding one scalar; generation only copies it.
    header = json.dumps({"fixture": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    header += b" " * (-len(header) % 8)
    (destination / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 4)


if __name__ == "__main__":
    create_checkpoint(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "text-generation")
