import argparse

import numpy as np
import tritonclient.http as httpclient
from transformers import AutoTokenizer


MODEL_ID = "distilbert-base-uncased-finetuned-sst-2-english"
MAX_LENGTH = 32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="localhost:8000")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    encoded = tokenizer(
        "The deployment flow is smooth and the model is fast.",
        padding="max_length",
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="np",
    )

    input_ids = encoded["input_ids"].astype(np.int64)
    attention_mask = encoded["attention_mask"].astype(np.int64)

    client = httpclient.InferenceServerClient(url=args.url)

    input_ids_tensor = httpclient.InferInput("input__0", input_ids.shape, "INT64")
    input_ids_tensor.set_data_from_numpy(input_ids)

    attention_mask_tensor = httpclient.InferInput(
        "input__1",
        attention_mask.shape,
        "INT64",
    )
    attention_mask_tensor.set_data_from_numpy(attention_mask)

    result = client.infer(
        model_name="distilbert_sentiment",
        inputs=[input_ids_tensor, attention_mask_tensor],
        outputs=[httpclient.InferRequestedOutput("output__0")],
    )

    logits = result.as_numpy("output__0")
    label = int(logits.argmax(axis=1)[0])
    sentiment = "positive" if label == 1 else "negative"

    print("Logits:", logits)
    print("Predicted label:", label)
    print("Predicted sentiment:", sentiment)


if __name__ == "__main__":
    main()
