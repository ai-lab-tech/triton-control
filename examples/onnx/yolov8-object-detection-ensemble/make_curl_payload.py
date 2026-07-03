import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def load_image(path: Path) -> np.ndarray:
    image = Image.open(path).convert("RGB").resize((640, 640))
    return np.asarray(image, dtype=np.uint8)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python make_curl_payload.py path/to/image.jpg")

    image = load_image(Path(sys.argv[1]))
    payload = {
        "inputs": [
            {
                "name": "IMAGE",
                "shape": [640, 640, 3],
                "datatype": "UINT8",
                "data": image.reshape(-1).tolist(),
            }
        ],
        "outputs": [
            {"name": "BOXES"},
            {"name": "SCORES"},
            {"name": "CLASS_IDS"},
        ],
    }

    print(json.dumps(payload))


if __name__ == "__main__":
    main()
