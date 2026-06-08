import { createAction, props } from "@ngrx/store";

export type InferenceMetricRow = {
  model: string;
  version: string;
  requestCount: number;
  totalMs: number;
  queueMs: number;
  withoutQueueMs: number;
  computeInputMs: number;
  computeInferMs: number;
  computeOutputMs: number;
};

export type InferenceMetrics = {
  available: boolean;
  error: string | null;
  source?: "metrics" | "stats";
  models: InferenceMetricRow[];
};

export const inferRequestStarted = createAction(
  "[Instances Infer] Request Started",
  props<{
    instanceId: string;
    modelName: string;
    version: string;
    payload: Record<string, unknown>;
  }>(),
);

export const inferRequestSucceeded = createAction(
  "[Instances Infer] Request Succeeded",
  props<{
    response: Record<string, unknown>;
    requestLatencyMs: number;
    inferenceMetrics: InferenceMetrics | null;
  }>(),
);

export const inferResultHydrated = createAction(
  "[Instances Infer] Result Hydrated",
  props<{
    responseJson: string;
    requestLatencyMs: number | null;
    inferenceMetrics: InferenceMetrics | null;
  }>(),
);

export const inferRequestFailed = createAction(
  "[Instances Infer] Request Failed",
  props<{ message: string; requestLatencyMs: number }>(),
);
