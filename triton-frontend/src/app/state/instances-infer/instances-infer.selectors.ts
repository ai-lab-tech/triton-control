import { instancesInferFeature } from "./instances-infer.reducer";

export const {
  selectSubmitting: selectInferSubmitting,
  selectProcessingResponse: selectInferProcessingResponse,
  selectError: selectInferError,
  selectResponseJson: selectInferResponseJson,
  selectRequestLatencyMs: selectInferRequestLatencyMs,
  selectInferenceMetrics: selectInferInferenceMetrics,
} = instancesInferFeature;
