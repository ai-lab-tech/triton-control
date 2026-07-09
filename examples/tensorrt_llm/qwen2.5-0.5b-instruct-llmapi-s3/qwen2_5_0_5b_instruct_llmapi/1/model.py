import json
import os
import sys
from pathlib import Path

import numpy as np
import triton_python_backend_utils as pb_utils


def _parameter(config, name, default):
    value = (config.get("parameters") or {}).get(name, {})
    if isinstance(value, dict):
        return value.get("string_value", default)
    return default


def _decode_text_input(array):
    value = array.reshape(-1)[0]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _extract_text(output):
    candidates = getattr(output, "outputs", None)
    if candidates:
        text = getattr(candidates[0], "text", None)
        if text is not None:
            return str(text)
    text = getattr(output, "text", None)
    if text is not None:
        return str(text)
    return str(output)


class TritonPythonModel:
    def initialize(self, args):
        config = json.loads(args["model_config"])
        version_dir = Path(args["model_repository"]) / args["model_version"]
        python_deps = version_dir / "python_deps"
        if python_deps.exists():
            deps_path = str(python_deps)
            sys.path.insert(0, deps_path)
            current_pythonpath = os.environ.get("PYTHONPATH", "")
            os.environ["PYTHONPATH"] = (
                deps_path if not current_pythonpath else f"{deps_path}:{current_pythonpath}"
            )

        from tensorrt_llm import LLM, SamplingParams

        model_dir = version_dir / _parameter(config, "hf_model_dir", "model")

        if not model_dir.exists():
            raise RuntimeError(f"Missing local Hugging Face model directory: {model_dir}")

        self.sampling_params = SamplingParams(
            max_tokens=int(_parameter(config, "max_tokens", "128")),
            temperature=float(_parameter(config, "temperature", "0.2")),
            top_p=float(_parameter(config, "top_p", "0.95")),
        )
        self.llm = LLM(model=str(model_dir), tensor_parallel_size=1)

    def execute(self, requests):
        responses = []
        for request in requests:
            tensor = pb_utils.get_input_tensor_by_name(request, "text_input")
            prompt = _decode_text_input(tensor.as_numpy())
            outputs = self.llm.generate([prompt], sampling_params=self.sampling_params)
            text = _extract_text(outputs[0]) if outputs else ""
            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("text_output", np.array([text.encode("utf-8")], dtype=object))
                    ]
                )
            )
        return responses

    def finalize(self):
        self.llm = None
