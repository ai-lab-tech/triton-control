import numpy as np
import triton_python_backend_utils as pb_utils


CONFIDENCE_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45
MAX_DETECTIONS = 100


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    if boxes.size == 0:
        return np.empty((0,), dtype=np.int64)

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = np.maximum(x2 - x1, 0.0) * np.maximum(y2 - y1, 0.0)
    order = scores.argsort()[::-1]
    keep = []

    while order.size > 0 and len(keep) < MAX_DETECTIONS:
        current = order[0]
        keep.append(current)

        xx1 = np.maximum(x1[current], x1[order[1:]])
        yy1 = np.maximum(y1[current], y1[order[1:]])
        xx2 = np.minimum(x2[current], x2[order[1:]])
        yy2 = np.minimum(y2[current], y2[order[1:]])

        width = np.maximum(xx2 - xx1, 0.0)
        height = np.maximum(yy2 - yy1, 0.0)
        intersection = width * height
        union = areas[current] + areas[order[1:]] - intersection
        iou = intersection / np.maximum(union, 1e-9)

        order = order[1:][iou <= iou_threshold]

    return np.array(keep, dtype=np.int64)


def decode_yolov8(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    predictions = raw.T
    boxes_xywh = predictions[:, :4]
    class_scores = predictions[:, 4:]

    class_ids = class_scores.argmax(axis=1).astype(np.int64)
    scores = class_scores.max(axis=1).astype(np.float32)
    mask = scores >= CONFIDENCE_THRESHOLD

    boxes_xywh = boxes_xywh[mask]
    scores = scores[mask]
    class_ids = class_ids[mask]

    if boxes_xywh.size == 0:
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
        )

    x_center, y_center, width, height = boxes_xywh.T
    boxes = np.stack(
        [
            x_center - width / 2.0,
            y_center - height / 2.0,
            x_center + width / 2.0,
            y_center + height / 2.0,
        ],
        axis=1,
    )
    boxes = np.clip(boxes / 640.0, 0.0, 1.0).astype(np.float32)

    selected = nms(boxes, scores, IOU_THRESHOLD)
    return boxes[selected], scores[selected], class_ids[selected]


class TritonPythonModel:
    def initialize(self, args):
        pass

    def execute(self, requests):
        responses = []

        for request in requests:
            raw_batch = pb_utils.get_input_tensor_by_name(
                request,
                "RAW_DETECTIONS",
            ).as_numpy()

            # The ensemble returns one variable-length output set per request.
            boxes, scores, class_ids = decode_yolov8(raw_batch[0])

            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("BOXES", boxes),
                        pb_utils.Tensor("SCORES", scores),
                        pb_utils.Tensor("CLASS_IDS", class_ids),
                    ]
                )
            )

        return responses

    def finalize(self):
        pass

