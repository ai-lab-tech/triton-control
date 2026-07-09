import argparse
from pathlib import Path

import numpy as np
import tritonclient.http as httpclient
from PIL import Image


def load_image(path: Path) -> np.ndarray:
    image = Image.open(path).convert("RGB").resize((640, 640))
    return np.asarray(image, dtype=np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path")
    parser.add_argument("--url", default="localhost:8000")
    args = parser.parse_args()

    image = load_image(Path(args.image_path))
    client = httpclient.InferenceServerClient(url=args.url)

    image_tensor = httpclient.InferInput("IMAGE", image.shape, "UINT8")
    image_tensor.set_data_from_numpy(image)

    result = client.infer(
        model_name="yolo_pipeline",
        inputs=[image_tensor],
        outputs=[
            httpclient.InferRequestedOutput("BOXES"),
            httpclient.InferRequestedOutput("SCORES"),
            httpclient.InferRequestedOutput("CLASS_IDS"),
        ],
    )

    boxes = result.as_numpy("BOXES")
    scores = result.as_numpy("SCORES")
    class_ids = result.as_numpy("CLASS_IDS")

    for index in range(min(len(scores), 10)):
        print(
            {
                "class_id": int(class_ids[index]),
                "score": float(scores[index]),
                "box": [float(value) for value in boxes[index]],
            }
        )


if __name__ == "__main__":
    main()
