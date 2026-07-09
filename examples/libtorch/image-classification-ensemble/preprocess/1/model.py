import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        self.mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        self.std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def execute(self, requests):
        responses = []

        for request in requests:
            images = pb_utils.get_input_tensor_by_name(request, "IMAGE").as_numpy()
            images = images.astype(np.float32) / 255.0
            images = (images - self.mean) / self.std
            images = np.transpose(images, (0, 3, 1, 2)).astype(np.float32)

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[pb_utils.Tensor("NORMALIZED_IMAGE", images)]
                )
            )

        return responses

    def finalize(self):
        pass

