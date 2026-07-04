import argparse

import numpy as np
import tritonclient.http as httpclient


DEFAULT_QUESTION = "What does Triton Control deploy?"
DEFAULT_CONTEXT = (
    "Triton Control helps teams create development workspaces, upload model "
    "repositories, and deploy NVIDIA Triton Inference Server instances."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--context", default=DEFAULT_CONTEXT)
    args = parser.parse_args()

    client = httpclient.InferenceServerClient(url=args.url)

    question = httpclient.InferInput("QUESTION", [1], "BYTES")
    question.set_data_from_numpy(np.array([args.question.encode("utf-8")], dtype=np.object_))

    context = httpclient.InferInput("CONTEXT", [1], "BYTES")
    context.set_data_from_numpy(np.array([args.context.encode("utf-8")], dtype=np.object_))

    result = client.infer(
        model_name="distilbert_squad_pipeline",
        inputs=[question, context],
        outputs=[
            httpclient.InferRequestedOutput("ANSWER"),
            httpclient.InferRequestedOutput("SCORE"),
        ],
    )

    answer = result.as_numpy("ANSWER")[0].decode("utf-8")
    score = float(result.as_numpy("SCORE")[0])
    print({"answer": answer, "score": score})


if __name__ == "__main__":
    main()
