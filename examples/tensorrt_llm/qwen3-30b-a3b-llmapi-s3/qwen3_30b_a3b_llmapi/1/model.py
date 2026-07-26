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


def _bool_parameter(config, name, default):
    value = str(_parameter(config, name, str(default))).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _int_parameter(config, name, default):
    return int(_parameter(config, name, str(default)))


def _float_parameter(config, name, default):
    return float(_parameter(config, name, str(default)))


def _decode_text_inputs(array):
    values = array.reshape(-1)
    prompts = []
    for value in values:
        if isinstance(value, bytes):
            prompts.append(value.decode("utf-8"))
        else:
            prompts.append(str(value))
    return prompts


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


def _optional_input_array(request, name):
    tensor = pb_utils.get_input_tensor_by_name(request, name)
    if tensor is None:
        return None
    return tensor.as_numpy()


def _first_string(array):
    if array is None:
        return None
    values = array.reshape(-1)
    if len(values) == 0:
        return None
    value = values[0]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


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
        from tensorrt_llm.llmapi import KvCacheConfig

        model_dir = version_dir / _parameter(config, "hf_model_dir", "model")
        if not model_dir.exists():
            raise RuntimeError(f"Missing local Hugging Face model directory: {model_dir}")

        self.default_sampling_config = {
            "max_tokens": _int_parameter(config, "max_tokens", "256"),
            "temperature": _float_parameter(config, "temperature", "0.6"),
            "top_p": _float_parameter(config, "top_p", "0.95"),
        }
        self.sampling_params_class = SamplingParams
        self.sampling_params = SamplingParams(**self.default_sampling_config)
        self.llm = LLM(
            model=str(model_dir),
            tensor_parallel_size=_int_parameter(config, "tensor_parallel_size", "1"),
            trust_remote_code=_bool_parameter(config, "trust_remote_code", "true"),
            max_batch_size=_int_parameter(config, "max_batch_size", "2"),
            max_seq_len=_int_parameter(config, "max_seq_len", "12000"),
            max_num_tokens=_int_parameter(config, "max_num_tokens", "4096"),
            kv_cache_config=KvCacheConfig(
                max_tokens=_int_parameter(config, "kv_cache_max_tokens", "4096"),
                free_gpu_memory_fraction=_float_parameter(
                    config, "kv_cache_free_gpu_memory_fraction", "0.70"
                ),
                enable_block_reuse=_bool_parameter(config, "enable_block_reuse", "false"),
            ),
        )

    def _sampling_params_for_request(self, request):
        raw_parameters = _first_string(_optional_input_array(request, "sampling_parameters"))
        if not raw_parameters:
            return self.sampling_params
        parameters = json.loads(raw_parameters)
        config = self.default_sampling_config
        return self.sampling_params_class(
            max_tokens=int(parameters.get("max_tokens", config["max_tokens"])),
            temperature=float(parameters.get("temperature", config["temperature"])),
            top_p=float(parameters.get("top_p", config["top_p"])),
        )

    def execute(self, requests):
        responses = []
        for request in requests:
            tensor = pb_utils.get_input_tensor_by_name(request, "text_input")
            prompts = _decode_text_inputs(tensor.as_numpy())
            sampling_params = self._sampling_params_for_request(request)
            outputs = self.llm.generate(prompts, sampling_params=sampling_params)
            texts = [_extract_text(output) for output in outputs]
            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor(
                            "text_output",
                            np.array(
                                [[text.encode("utf-8")] for text in texts],
                                dtype=object,
                            ),
                        )
                    ]
                )
            )
        return responses

    def finalize(self):
        self.llm = None
