import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", default="request.json")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    image = [0] * (224 * 224 * 3)
    payload = {
        "inputs": [
            {
                "name": "IMAGE",
                "shape": [1, 224, 224, 3],
                "datatype": "UINT8",
                "data": image,
            }
        ],
        "outputs": [{"name": "PROBABILITIES"}],
    }
    content = json.dumps(payload)
    if args.stdout:
        print(content)
        return

    output_path = Path(args.output)
    output_path.write_text(content, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
