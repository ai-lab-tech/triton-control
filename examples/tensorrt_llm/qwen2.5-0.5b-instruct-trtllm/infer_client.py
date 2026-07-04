import argparse
import json

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000", help="Triton HTTP endpoint")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Write a short checklist for deploying TensorRT-LLM with Triton Control.",
    )
    args = parser.parse_args()

    endpoint = f"http://{args.url}/v2/models/qwen2_5_0_5b_instruct_trtllm/generate"
    payload = {
        "text_input": args.prompt,
        "sampling_param_max_tokens": args.max_tokens,
        "sampling_param_temperature": args.temperature,
        "sampling_param_top_p": 0.95,
        "sampling_param_exclude_input_from_output": True,
    }

    response = requests.post(endpoint, json=payload, timeout=300)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
