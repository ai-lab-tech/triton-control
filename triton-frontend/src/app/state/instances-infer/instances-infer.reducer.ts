import { createFeature, createReducer, on } from "@ngrx/store";
import {
  type InferenceMetrics,
  inferResultHydrated,
  inferRequestFailed,
  inferRequestStarted,
  inferRequestSucceeded,
} from "./instances-infer.actions";

export const INSTANCES_INFER_FEATURE_KEY = "instancesInfer";

export interface InstancesInferState {
  submitting: boolean;
  processingResponse: boolean;
  error: string;
  responseJson: string;
  requestLatencyMs: number | null;
  inferenceMetrics: InferenceMetrics | null;
}

export const initialInstancesInferState: InstancesInferState = {
  submitting: false,
  processingResponse: false,
  error: "",
  responseJson: "",
  requestLatencyMs: null,
  inferenceMetrics: null,
};

export const instancesInferReducer = createReducer(
  initialInstancesInferState,
  on(inferRequestStarted, () => ({
    ...initialInstancesInferState,
    submitting: true,
  })),
  on(inferRequestSucceeded, (_state, { response, requestLatencyMs, inferenceMetrics }) => ({
    submitting: false,
    processingResponse: false,
    error: "",
    responseJson: JSON.stringify(response, null, 2),
    requestLatencyMs,
    inferenceMetrics,
  })),
  on(inferResultHydrated, (_state, { responseJson, requestLatencyMs, inferenceMetrics }) => ({
    submitting: false,
    processingResponse: false,
    error: "",
    responseJson,
    requestLatencyMs,
    inferenceMetrics,
  })),
  on(inferRequestFailed, (_state, { message, requestLatencyMs }) => ({
    submitting: false,
    processingResponse: false,
    error: message,
    responseJson: "",
    requestLatencyMs,
    inferenceMetrics: null,
  })),
);

export const instancesInferFeature = createFeature({
  name: INSTANCES_INFER_FEATURE_KEY,
  reducer: instancesInferReducer,
});
