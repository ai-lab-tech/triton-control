from pathlib import Path

import numpy as np
import triton_python_backend_utils as pb_utils
from transformers import AutoTokenizer


MODEL_NAME = "distilbert-base-cased-distilled-squad"
MAX_LENGTH = 384


def _to_text(value: np.ndarray) -> str:
    item = value.reshape(-1)[0]
    if isinstance(item, bytes):
        return item.decode("utf-8")
    return str(item)


class TritonPythonModel:
    def initialize(self, args):
        tokenizer_dir = Path(args["model_repository"]) / args["model_version"] / "tokenizer"
        self.tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_dir if tokenizer_dir.exists() else MODEL_NAME
        )

    def execute(self, requests):
        responses = []

        for request in requests:
            question = _to_text(pb_utils.get_input_tensor_by_name(request, "QUESTION").as_numpy())
            context = _to_text(pb_utils.get_input_tensor_by_name(request, "CONTEXT").as_numpy())

            encoded = self.tokenizer(
                question,
                context,
                max_length=MAX_LENGTH,
                padding="max_length",
                truncation="only_second",
                return_offsets_mapping=True,
                return_tensors="np",
            )

            input_ids = encoded["input_ids"].astype(np.int32)
            attention_mask = encoded["attention_mask"].astype(np.int32)
            offsets = encoded["offset_mapping"].astype(np.int32)
            context_bytes = np.array([context.encode("utf-8")], dtype=np.object_)

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("INPUT_IDS", input_ids),
                        pb_utils.Tensor("ATTENTION_MASK", attention_mask),
                        pb_utils.Tensor("OFFSET_MAPPING", offsets),
                        pb_utils.Tensor("CONTEXT_TEXT", context_bytes),
                    ]
                )
            )

        return responses

    def finalize(self):
        pass
