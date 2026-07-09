import argparse

import numpy as np
import tritonclient.http as httpclient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000")
    args = parser.parse_args()

    client = httpclient.InferenceServerClient(url=args.url)

    # One real-shaped sample from the breast cancer feature space.
    features = np.array(
        [
            [
                17.99,
                10.38,
                122.80,
                1001.0,
                0.11840,
                0.27760,
                0.30010,
                0.14710,
                0.24190,
                0.07871,
                1.0950,
                0.9053,
                8.589,
                153.40,
                0.006399,
                0.04904,
                0.05373,
                0.01587,
                0.03003,
                0.006193,
                25.38,
                17.33,
                184.60,
                2019.0,
                0.16220,
                0.66560,
                0.71190,
                0.26540,
                0.46010,
                0.11890,
            ]
        ],
        dtype=np.float32,
    )

    input_tensor = httpclient.InferInput("FEATURES", features.shape, "FP32")
    input_tensor.set_data_from_numpy(features)

    result = client.infer(
        model_name="breast_cancer_classifier",
        inputs=[input_tensor],
        outputs=[
            httpclient.InferRequestedOutput("CLASS_ID"),
            httpclient.InferRequestedOutput("PROBABILITIES"),
        ],
    )

    print("Class ID:", result.as_numpy("CLASS_ID").reshape(-1).tolist())
    print("Probabilities:", result.as_numpy("PROBABILITIES"))


if __name__ == "__main__":
    main()
