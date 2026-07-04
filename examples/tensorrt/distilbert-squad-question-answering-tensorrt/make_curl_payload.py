import json


QUESTION = "What does Triton Control deploy?"
CONTEXT = (
    "Triton Control helps teams create development workspaces, upload model "
    "repositories, and deploy NVIDIA Triton Inference Server instances."
)


def main() -> None:
    payload = {
        "inputs": [
            {
                "name": "QUESTION",
                "shape": [1],
                "datatype": "BYTES",
                "data": [QUESTION],
            },
            {
                "name": "CONTEXT",
                "shape": [1],
                "datatype": "BYTES",
                "data": [CONTEXT],
            },
        ],
        "outputs": [
            {"name": "ANSWER"},
            {"name": "SCORE"},
        ],
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
