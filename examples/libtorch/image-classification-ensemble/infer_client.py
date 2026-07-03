import argparse

import numpy as np
import tritonclient.http as httpclient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000")
    args = parser.parse_args()

    client = httpclient.InferenceServerClient(url=args.url)

    images = np.random.randint(0, 256, size=(1, 224, 224, 3), dtype=np.uint8)

    input_tensor = httpclient.InferInput("IMAGE", images.shape, "UINT8")
    input_tensor.set_data_from_numpy(images)

    result = client.infer(
        model_name="image_pipeline",
        inputs=[input_tensor],
        outputs=[httpclient.InferRequestedOutput("PROBABILITIES")],
    )

    scores = result.as_numpy("PROBABILITIES")
    print("Output shape:", scores.shape)
    print("Predicted class index:", int(scores.argmax(axis=1)[0]))


if __name__ == "__main__":
    main()
