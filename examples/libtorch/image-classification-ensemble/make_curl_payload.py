import json


def main() -> None:
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
    print(json.dumps(payload))


if __name__ == "__main__":
    main()

