import os

import numpy as np
import triton_python_backend_utils as pb_utils
from joblib import load


class TritonPythonModel:
    def initialize(self, args):
        model_path = os.path.join(os.path.dirname(__file__), "model.joblib")
        self.model = load(model_path)

    def execute(self, requests):
        responses = []

        for request in requests:
            features = pb_utils.get_input_tensor_by_name(request, "FEATURES").as_numpy()

            class_ids = self.model.predict(features).astype(np.int64).reshape(-1, 1)
            probabilities = self.model.predict_proba(features).astype(np.float32)

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("CLASS_ID", class_ids),
                        pb_utils.Tensor("PROBABILITIES", probabilities),
                    ]
                )
            )

        return responses

    def finalize(self):
        pass
