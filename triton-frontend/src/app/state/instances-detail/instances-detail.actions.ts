import { createAction, props } from "@ngrx/store";

import { type Instance } from "../../pages/instances/instances.data";
import {
  type UpdateInstanceS3Request,
  type UpdateTritonInstanceRequest,
} from "../../api/generated/index";

export const instanceDetailPageOpened = createAction(
  "[Instances Detail] Page Opened",
  props<{ instanceId: string }>(),
);

export const instanceDetailLoaded = createAction(
  "[Instances Detail] Loaded",
  props<{ instance: Instance }>(),
);

export const instanceDetailSupplementLoaded = createAction(
  "[Instances Detail] Supplement Loaded",
  props<{
    partial: Partial<Pick<Instance, "assignedUsers" | "s3" | "repositoryModels">>;
  }>(),
);

export const instanceDetailLoadFailed = createAction(
  "[Instances Detail] Load Failed",
  props<{ message: string }>(),
);

export const instanceDetailRefreshRequested = createAction(
  "[Instances Detail] Refresh Requested",
  props<{ instanceId: string }>(),
);

export const instanceDetailRefreshed = createAction(
  "[Instances Detail] Refreshed",
  props<{ partial: Partial<Instance> }>(),
);

export const s3ConfigSaveRequested = createAction(
  "[Instances Detail] S3 Config Save Requested",
  props<{ instanceId: string; payload: UpdateInstanceS3Request }>(),
);

export const s3ConfigSaveSucceeded = createAction(
  "[Instances Detail] S3 Config Save Succeeded",
  props<{ s3: Instance["s3"] }>(),
);

export const s3ConfigSaveFailed = createAction(
  "[Instances Detail] S3 Config Save Failed",
  props<{ message: string }>(),
);

export const s3ConfigDisableRequested = createAction(
  "[Instances Detail] S3 Config Disable Requested",
  props<{ instanceId: string; currentS3: Instance["s3"] }>(),
);

export const s3ConfigDisableSucceeded = createAction(
  "[Instances Detail] S3 Config Disable Succeeded",
  props<{ s3: Instance["s3"] }>(),
);

export const s3ConfigDisableFailed = createAction(
  "[Instances Detail] S3 Config Disable Failed",
  props<{ message: string }>(),
);

export const tritonConnectionSaveRequested = createAction(
  "[Instances Detail] Triton Connection Save Requested",
  props<{ instanceId: string; payload: UpdateTritonInstanceRequest }>(),
);

export const tritonConnectionSaveSucceeded = createAction(
  "[Instances Detail] Triton Connection Save Succeeded",
  props<{ partial: Partial<Instance> }>(),
);

export const tritonConnectionSaveFailed = createAction(
  "[Instances Detail] Triton Connection Save Failed",
  props<{ message: string }>(),
);

export const instanceDetailRefreshSilentlyFailed = createAction(
  "[Instances Detail] Refresh Silently Failed",
);
