import argparse
import json

import requests


MODEL_NAME = "qwen3_30b_a3b_llmapi"


def output_by_name(response, name):
    for output in response.get("outputs", []):
        if output.get("name") == name:
            return output
    raise KeyError(f"Missing output {name!r}: {response}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000", help="Triton HTTP endpoint")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?",
    )
    args = parser.parse_args()
    sampling_parameters = {
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
    }

    payload = {
        "inputs": [
            {
                "name": "text_input",
                "shape": [1, 1],
                "datatype": "BYTES",
                "data": [args.prompt],
            },
            {
                "name": "stream",
                "shape": [1, 1],
                "datatype": "BOOL",
                "data": [False],
            },
            {
                "name": "sampling_parameters",
                "shape": [1, 1],
                "datatype": "BYTES",
                "data": [json.dumps(sampling_parameters)],
            },
            {
                "name": "exclude_input_in_output",
                "shape": [1, 1],
                "datatype": "BOOL",
                "data": [True],
            }
        ],
        "outputs": [{"name": "text_output"}],
    }
    endpoint = f"http://{args.url}/v2/models/{MODEL_NAME}/infer"
    response = requests.post(endpoint, json=payload, timeout=300)
    response.raise_for_status()
    result = response.json()

    text = output_by_name(result, "text_output").get("data", [""])[0]
    print(text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
