import { createFeature, createReducer, on } from "@ngrx/store";

import { type Instance } from "../../pages/instances/instances.data";
import {
  instanceDetailLoaded,
  instanceDetailLoadFailed,
  instanceDetailPageOpened,
  instanceDetailRefreshed,
  instanceDetailSupplementLoaded,
  s3ConfigDisableRequested,
  s3ConfigDisableSucceeded,
  s3ConfigSaveRequested,
  s3ConfigSaveSucceeded,
  tritonConnectionSaveFailed,
  tritonConnectionSaveRequested,
  tritonConnectionSaveSucceeded,
} from "./instances-detail.actions";

export const INSTANCES_DETAIL_FEATURE_KEY = "instancesDetail";

export interface InstancesDetailState {
  instance: Instance | null;
  loading: boolean;
  error: string | null;
  s3Saving: boolean;
  tritonSaving: boolean;
}

export const initialInstancesDetailState: InstancesDetailState = {
  instance: null,
  loading: false,
  error: null,
  s3Saving: false,
  tritonSaving: false,
};

export const instancesDetailReducer = createReducer(
  initialInstancesDetailState,
  on(instanceDetailPageOpened, () => ({
    ...initialInstancesDetailState,
    loading: true,
  })),
  on(instanceDetailLoaded, (_state, { instance }) => ({
    instance,
    loading: false,
    error: null,
    s3Saving: false,
    tritonSaving: false,
  })),
  on(instanceDetailLoadFailed, (_state, { message }) => ({
    instance: null,
    loading: false,
    error: message,
    s3Saving: false,
    tritonSaving: false,
  })),
  on(instanceDetailRefreshed, (state, { partial }) => ({
    ...state,
    instance: state.instance ? { ...state.instance, ...partial } : state.instance,
  })),
  on(instanceDetailSupplementLoaded, (state, { partial }) => ({
    ...state,
    instance: state.instance ? { ...state.instance, ...partial } : state.instance,
  })),
  on(s3ConfigSaveRequested, (state) => ({ ...state, s3Saving: true })),
  on(s3ConfigDisableRequested, (state) => ({ ...state, s3Saving: true })),
  on(s3ConfigSaveSucceeded, (state, { s3 }) => ({
    ...state,
    s3Saving: false,
    instance: state.instance ? { ...state.instance, s3 } : state.instance,
  })),
  on(s3ConfigDisableSucceeded, (state, { s3 }) => ({
    ...state,
    s3Saving: false,
    instance: state.instance ? { ...state.instance, s3 } : state.instance,
  })),
  on(tritonConnectionSaveRequested, (state) => ({ ...state, tritonSaving: true })),
  on(tritonConnectionSaveSucceeded, (state, { partial }) => ({
    ...state,
    tritonSaving: false,
    instance: state.instance ? { ...state.instance, ...partial } : state.instance,
  })),
  on(tritonConnectionSaveFailed, (state) => ({ ...state, tritonSaving: false })),
);

export const instancesDetailFeature = createFeature({
  name: INSTANCES_DETAIL_FEATURE_KEY,
  reducer: instancesDetailReducer,
});
