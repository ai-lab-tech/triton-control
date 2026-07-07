import json
from pathlib import Path


OUTPUT_PATH = Path(__file__).with_name("request.json")


def main() -> None:
    test_image = [0] * (640 * 640 * 3)
    payload = {
        "inputs": [
            {
                "name": "IMAGE",
                "shape": [640, 640, 3],
                "datatype": "UINT8",
                "data": test_image,
            }
        ],
        "outputs": [
            {"name": "BOXES"},
            {"name": "SCORES"},
            {"name": "CLASS_IDS"},
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
