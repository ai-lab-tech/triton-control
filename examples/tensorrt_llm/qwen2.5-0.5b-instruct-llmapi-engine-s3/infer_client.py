import argparse
import json
from pathlib import Path

import requests
from transformers import AutoTokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--url", default="localhost:8000")
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument(
        "--tokenizer",
        type=Path,
        default=Path(__file__).resolve().parent
        / "qwen2_5_0_5b_instruct_llmapi_engine"
        / "1",
    )
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    input_ids = tokenizer.encode(args.prompt, add_special_tokens=True)
    end_id = tokenizer.eos_token_id
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else end_id

    endpoint = (
        f"http://{args.url}/v2/models/qwen2_5_0_5b_instruct_llmapi_engine/infer"
    )
    payload = {
        "inputs": [
            {
                "name": "input_ids",
                "shape": [1, len(input_ids)],
                "datatype": "INT32",
                "data": [input_ids],
            },
            {
                "name": "input_lengths",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [[len(input_ids)]],
            },
            {
                "name": "request_output_len",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [[args.max_tokens]],
            },
            {
                "name": "end_id",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [[end_id]],
            },
            {
                "name": "pad_id",
                "shape": [1, 1],
                "datatype": "INT32",
                "data": [[pad_id]],
            },
        ],
        "outputs": [{"name": "output_ids"}, {"name": "sequence_length"}],
    }

    response = requests.post(endpoint, json=payload, timeout=120)
    response.raise_for_status()
    result = response.json()
    outputs = {output["name"]: output for output in result["outputs"]}
    output_ids = outputs["output_ids"]["data"]
    sequence_length = outputs["sequence_length"]["data"][0]
    generated_ids = output_ids[:sequence_length]
    print(tokenizer.decode(generated_ids, skip_special_tokens=True))


if __name__ == "__main__":
    main()
