import { createFeature, createReducer, on } from "@ngrx/store";

import { type Instance } from "../../pages/instances/instances.data";
import {
  createInstanceFailed,
  createInstanceRequested,
  createInstanceSucceeded,
  deleteInstanceFailed,
  deleteInstanceRequested,
  deleteInstanceSucceeded,
  instancesListLoaded,
  instancesListLoadFailed,
  instancesListPageOpened,
  instancesListRefreshRequested,
} from "./instances-list.actions";

export const INSTANCES_LIST_FEATURE_KEY = "instancesList";

export interface InstancesListState {
  instances: Instance[];
  loading: boolean;
  creating: boolean;
  deleting: boolean;
  createError: string | null;
  deleteError: string | null;
}

export const initialInstancesListState: InstancesListState = {
  instances: [],
  loading: false,
  creating: false,
  deleting: false,
  createError: null,
  deleteError: null,
};

export const instancesListReducer = createReducer(
  initialInstancesListState,
  on(instancesListPageOpened, (state) => ({ ...state, loading: true })),
  on(instancesListRefreshRequested, (state) => ({ ...state, loading: true })),
  on(instancesListLoaded, (_state, { instances }) => ({
    instances,
    loading: false,
    creating: false,
    deleting: false,
    createError: null,
    deleteError: null,
  })),
  on(instancesListLoadFailed, (state) => ({ ...state, loading: false })),
  on(createInstanceRequested, (state) => ({ ...state, creating: true, createError: null })),
  on(createInstanceSucceeded, (state) => ({ ...state, creating: false })),
  on(createInstanceFailed, (state, { message }) => ({
    ...state,
    creating: false,
    createError: message,
  })),
  on(deleteInstanceRequested, (state) => ({ ...state, deleting: true, deleteError: null })),
  on(deleteInstanceSucceeded, (state) => ({ ...state, deleting: false })),
  on(deleteInstanceFailed, (state, { message }) => ({
    ...state,
    deleting: false,
    deleteError: message,
  })),
);

export const instancesListFeature = createFeature({
  name: INSTANCES_LIST_FEATURE_KEY,
  reducer: instancesListReducer,
});
