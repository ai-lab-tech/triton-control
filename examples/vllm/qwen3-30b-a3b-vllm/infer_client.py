import argparse
import asyncio
import json

import numpy as np
import tritonclient.grpc.aio as grpcclient


async def infer(url: str, prompt: str, max_tokens: int, temperature: float) -> None:
    client = grpcclient.InferenceServerClient(url=url)
    sampling_parameters = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
    }

    inputs = []
    inputs.append(grpcclient.InferInput("text_input", [1], "BYTES"))
    inputs[-1].set_data_from_numpy(np.array([prompt.encode("utf-8")], dtype=np.object_))

    inputs.append(grpcclient.InferInput("stream", [1], "BOOL"))
    inputs[-1].set_data_from_numpy(np.array([False], dtype=bool))

    inputs.append(grpcclient.InferInput("sampling_parameters", [1], "BYTES"))
    inputs[-1].set_data_from_numpy(
        np.array([json.dumps(sampling_parameters).encode("utf-8")], dtype=np.object_)
    )

    inputs.append(grpcclient.InferInput("exclude_input_in_output", [1], "BOOL"))
    inputs[-1].set_data_from_numpy(np.array([True], dtype=bool))

    async def requests():
        yield {
            "model_name": "qwen3_30b_a3b",
            "inputs": inputs,
            "outputs": [grpcclient.InferRequestedOutput("text_output")],
            "request_id": "0",
            "parameters": sampling_parameters,
        }

    chunks = []
    async for response in client.stream_infer(inputs_iterator=requests()):
        result, error = response
        if error:
            raise error
        for item in result.as_numpy("text_output"):
            chunks.append(item.decode("utf-8"))

    print("".join(chunks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8001", help="Triton gRPC endpoint")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Explain when to reduce max_model_len for Qwen3 30B A3B.",
    )
    args = parser.parse_args()
    asyncio.run(infer(args.url, args.prompt, args.max_tokens, args.temperature))


if __name__ == "__main__":
    main()
