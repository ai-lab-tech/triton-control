import { createAction, props } from "@ngrx/store";

import { type Instance } from "../../pages/instances/instances.data";

export const instancesListPageOpened = createAction("[Instances List] Page Opened");

export const instancesListRefreshRequested = createAction("[Instances List] Refresh Requested");

export const instancesListLoaded = createAction(
  "[Instances List] Loaded",
  props<{ instances: Instance[] }>(),
);

export const instancesListLoadFailed = createAction(
  "[Instances List] Load Failed",
  props<{ message: string }>(),
);

export const createInstanceRequested = createAction(
  "[Instances List] Create Instance Requested",
  props<{
    name?: string;
    url: string;
    verifySsl?: boolean;
    caCertificate?: string;
    metricsUrl?: string;
  }>(),
);

export const createInstanceSucceeded = createAction("[Instances List] Create Instance Succeeded");

export const createInstanceFailed = createAction(
  "[Instances List] Create Instance Failed",
  props<{ message: string }>(),
);

export const deleteInstanceRequested = createAction(
  "[Instances List] Delete Instance Requested",
  props<{ instanceId: string; instanceName: string; isSelfDeployed?: boolean }>(),
);

export const deleteInstanceSucceeded = createAction(
  "[Instances List] Delete Instance Succeeded",
  props<{ instanceName: string }>(),
);

export const deleteInstanceFailed = createAction(
  "[Instances List] Delete Instance Failed",
  props<{ message: string }>(),
);
