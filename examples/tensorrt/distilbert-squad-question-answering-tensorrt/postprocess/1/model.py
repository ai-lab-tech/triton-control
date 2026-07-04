import numpy as np
import triton_python_backend_utils as pb_utils


MAX_ANSWER_TOKENS = 30


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    return exp / np.maximum(exp.sum(), 1e-9)


def _to_text(value: np.ndarray) -> str:
    item = value.reshape(-1)[0]
    if isinstance(item, bytes):
        return item.decode("utf-8")
    return str(item)


def _best_answer(
    context: str,
    offsets: np.ndarray,
    start_logits: np.ndarray,
    end_logits: np.ndarray,
) -> tuple[str, float]:
    start_probs = _softmax(start_logits.astype(np.float32))
    end_probs = _softmax(end_logits.astype(np.float32))

    best_score = -1.0
    best_span = (0, 0)

    for start in np.argsort(start_probs)[-20:]:
        for end in np.argsort(end_probs)[-20:]:
            if end < start or end - start + 1 > MAX_ANSWER_TOKENS:
                continue

            char_start, _ = offsets[start]
            _, char_end = offsets[end]
            if char_start == 0 and char_end == 0:
                continue
            if char_end <= char_start:
                continue

            score = float(start_probs[start] * end_probs[end])
            if score > best_score:
                best_score = score
                best_span = (int(char_start), int(char_end))

    answer = context[best_span[0] : best_span[1]].strip()
    return answer, max(best_score, 0.0)


class TritonPythonModel:
    def initialize(self, args):
        pass

    def execute(self, requests):
        responses = []

        for request in requests:
            context = _to_text(pb_utils.get_input_tensor_by_name(request, "CONTEXT_TEXT").as_numpy())
            offsets = pb_utils.get_input_tensor_by_name(request, "OFFSET_MAPPING").as_numpy()[0]
            start_logits = pb_utils.get_input_tensor_by_name(request, "START_LOGITS").as_numpy()[0]
            end_logits = pb_utils.get_input_tensor_by_name(request, "END_LOGITS").as_numpy()[0]

            answer, score = _best_answer(context, offsets, start_logits, end_logits)

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("ANSWER", np.array([answer.encode("utf-8")], dtype=np.object_)),
                        pb_utils.Tensor("SCORE", np.array([score], dtype=np.float32)),
                    ]
                )
            )

        return responses

    def finalize(self):
        pass
