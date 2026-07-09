import argparse
import json

import requests


MODEL_NAME = "qwen2_5_0_5b_instruct_llmapi"


def output_by_name(response, name):
    for output in response.get("outputs", []):
        if output.get("name") == name:
            return output
    raise KeyError(f"Missing output {name!r}: {response}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000", help="Triton HTTP endpoint")
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Give me a compact TensorRT-LLM LLM API deployment checklist.",
    )
    args = parser.parse_args()

    payload = {
        "inputs": [
            {
                "name": "text_input",
                "shape": [1],
                "datatype": "BYTES",
                "data": [args.prompt],
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
