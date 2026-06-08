import { createAction, props } from "@ngrx/store";

export const profileRunStarted = createAction(
  "[Instances Profile] Run Started",
  props<{
    key: string;
    instanceId: string;
    modelName: string;
    version: string;
    batchSize: number;
    concurrencyRange: string;
    measurementRequestCount: number;
    inputData?: string;
  }>(),
);

export const profilePageOpened = createAction(
  "[Instances Profile] Page Opened",
  props<{ key: string }>(),
);

export const profileLastResultLoadStarted = createAction(
  "[Instances Profile] Last Result Load Started",
  props<{
    key: string;
    instanceId: string;
    modelName: string;
    version: string;
  }>(),
);

export const profileLastResultLoadSucceeded = createAction(
  "[Instances Profile] Last Result Load Succeeded",
  props<{
    key: string;
    batchSize?: number;
    concurrencyRange?: string;
    measurementRequestCount?: number;
    inputData?: string;
    command: string[];
    output: string;
  }>(),
);

export const profileLastResultLoadSkipped = createAction(
  "[Instances Profile] Last Result Load Skipped",
  props<{ key: string }>(),
);

export const profileRunSucceeded = createAction(
  "[Instances Profile] Run Succeeded",
  props<{
    key: string;
    command: string[];
    output: string;
  }>(),
);

export const profileRunFailed = createAction(
  "[Instances Profile] Run Failed",
  props<{ key: string; message: string }>(),
);
