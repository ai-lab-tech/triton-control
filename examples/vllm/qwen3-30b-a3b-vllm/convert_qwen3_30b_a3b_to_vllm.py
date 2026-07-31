import json
from pathlib import Path

from huggingface_hub import snapshot_download


MODEL_ID = "Qwen/Qwen3-30B-A3B"
MODEL_ROOT = Path("qwen3_30b_a3b/1")
MODEL_DIR = MODEL_ROOT / "model"
MODEL_JSON = MODEL_ROOT / "model.json"


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Preparing {MODEL_ID} under {MODEL_DIR}")

    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=MODEL_DIR,
        ignore_patterns=[".git*", "*.ckpt", "*.h5", "*.msgpack", "*.onnx"],
    )

    print(f"Downloaded model snapshot to {MODEL_DIR}")

    engine_config = {
        "model": "./model",
        "tokenizer": "./model",
        "dtype": "auto",
        "max_model_len": 12000,
        "max_num_seqs": 2,
        "max_num_batched_tokens": 4096,
        "tensor_parallel_size": 1,
        "gpu_memory_utilization": 0.7,
        "trust_remote_code": True,
        "enable_prefix_caching": False,
        "enforce_eager": True,
    }

    MODEL_JSON.write_text(json.dumps(engine_config, indent=2) + "\n", encoding="utf-8")
    print(MODEL_JSON.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
