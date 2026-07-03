import argparse

import numpy as np
import tritonclient.http as httpclient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000")
    args = parser.parse_args()

    client = httpclient.InferenceServerClient(url=args.url)

    features = np.array(
        [
            [5.1, 3.5, 1.4, 0.2],
            [6.7, 3.1, 4.7, 1.5],
        ],
        dtype=np.float32,
    )

    input_tensor = httpclient.InferInput("FEATURES", features.shape, "FP32")
    input_tensor.set_data_from_numpy(features)

    result = client.infer(
        model_name="iris_classifier",
        inputs=[input_tensor],
        outputs=[
            httpclient.InferRequestedOutput("CLASS_ID"),
            httpclient.InferRequestedOutput("PROBABILITIES"),
        ],
    )

    print("Class IDs:", result.as_numpy("CLASS_ID").reshape(-1).tolist())
    print("Probabilities:", result.as_numpy("PROBABILITIES"))


if __name__ == "__main__":
    main()
