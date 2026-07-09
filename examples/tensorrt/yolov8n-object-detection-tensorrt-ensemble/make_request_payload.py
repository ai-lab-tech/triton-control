import argparse
import json
from pathlib import Path


IMAGE_SIZE = 640
PIXEL_COUNT = IMAGE_SIZE * IMAGE_SIZE * 3
DEFAULT_OUTPUT = Path(__file__).with_name("request.json")


def load_image(path: Path) -> list[int]:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "Image input requires Pillow and NumPy. Install them with: "
            "python3 -m pip install pillow numpy"
        ) from exc

    image = Image.open(path).convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE))
    return np.asarray(image, dtype=np.uint8).reshape(-1).tolist()


def empty_image() -> list[int]:
    return [0] * PIXEL_COUNT


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a Triton HTTP request body for yolov8_trt_pipeline."
    )
    parser.add_argument(
        "image_path",
        nargs="?",
        help="Optional image file. If omitted, writes an all-black smoke-test image.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON file. Defaults to request.json next to this script.",
    )
    args = parser.parse_args()

    image_data = load_image(Path(args.image_path)) if args.image_path else empty_image()
    payload = {
        "inputs": [
            {
                "name": "IMAGE",
                "shape": [IMAGE_SIZE, IMAGE_SIZE, 3],
                "datatype": "UINT8",
                "data": image_data,
            }
        ],
        "outputs": [
            {"name": "BOXES"},
            {"name": "SCORES"},
            {"name": "CLASS_IDS"},
        ],
    }

    args.output.write_text(json.dumps(payload), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
