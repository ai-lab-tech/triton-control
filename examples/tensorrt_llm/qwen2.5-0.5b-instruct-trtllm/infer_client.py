import argparse
import json
from pathlib import Path
from typing import Any

import requests
from transformers import AutoTokenizer


MODEL_NAME = "qwen2_5_0_5b_instruct_trtllm"
HF_MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"


def output_by_name(response: dict[str, Any], name: str) -> dict[str, Any]:
    for output in response.get("outputs", []):
        if output.get("name") == name:
            return output
    raise KeyError(f"Missing output {name!r}: {response}")


def build_payload(prompt: str, max_tokens: int) -> tuple[dict[str, Any], Any]:
    local_model = Path(MODEL_NAME) / "1" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(local_model if local_model.exists() else HF_MODEL_ID)
    input_ids = tokenizer.encode(prompt, add_special_tokens=True)
    payload = {
        "inputs": [
            {
                "name": "input_ids",
                "shape": [1, len(input_ids)],
                "datatype": "INT32",
                "data": input_ids,
            },
            {
                "name": "input_lengths",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [len(input_ids)],
            },
            {
                "name": "request_output_len",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [max_tokens],
            },
        ],
        "outputs": [
            {"name": "output_ids"},
            {"name": "sequence_length"},
        ],
    }
    return payload, tokenizer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000", help="Triton HTTP endpoint")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--print-payload",
        action="store_true",
        help="Print the Triton /infer JSON payload for use in Triton Control.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Write a short checklist for deploying TensorRT-LLM with Triton Control.",
    )
    args = parser.parse_args()

    payload, tokenizer = build_payload(args.prompt, args.max_tokens)

    if args.print_payload:
        print(json.dumps(payload, indent=2))
        return

    endpoint = f"http://{args.url}/v2/models/{MODEL_NAME}/infer"
    response = requests.post(endpoint, json=payload, timeout=300)
    response.raise_for_status()
    result = response.json()

    output_ids = output_by_name(result, "output_ids")
    sequence_length = output_by_name(result, "sequence_length")
    ids = output_ids["data"]
    shape = output_ids.get("shape", [])

    if shape:
        token_count = int(shape[-1])
        ids = ids[:token_count]
    if sequence_length.get("data"):
        ids = ids[: int(sequence_length["data"][0])]

    print(tokenizer.decode(ids, skip_special_tokens=True))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
