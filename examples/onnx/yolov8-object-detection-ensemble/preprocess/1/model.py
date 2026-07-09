import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        pass

    def execute(self, requests):
        responses = []

        for request in requests:
            images = pb_utils.get_input_tensor_by_name(request, "IMAGE").as_numpy()
            if images.ndim == 3:
                images = images[None, ...]
            images = images.astype(np.float32) / 255.0
            images = np.transpose(images, (0, 3, 1, 2)).astype(np.float32)

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[pb_utils.Tensor("PREPROCESSED_IMAGE", images)]
                )
            )

        return responses

    def finalize(self):
        pass
