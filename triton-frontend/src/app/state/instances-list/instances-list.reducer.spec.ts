/* eslint-disable @typescript-eslint/no-explicit-any */
import { instancesListReducer, initialInstancesListState } from "./instances-list.reducer";
import {
  instancesListPageOpened,
  instancesListRefreshRequested,
  instancesListLoaded,
  instancesListLoadFailed,
  createInstanceRequested,
  createInstanceSucceeded,
  createInstanceFailed,
  deleteInstanceFailed,
  deleteInstanceRequested,
  deleteInstanceSucceeded,
} from "./instances-list.actions";

const MOCK_INSTANCE: any = {
  id: "1",
  name: "node-1",
  url: "http://localhost:8000",
  status: "healthy",
  version: "1.0",
  region: "Unknown",
  models: 0,
  healthLive: true,
  healthReady: true,
  healthLastCheckedAt: "",
  healthError: "",
  serverMetadata: null,
  qps: 0,
  cpu: 0,
  ram: 0,
  gpu: 0,
  assignedUsers: [],
  s3: {
    enabled: false,
    endpoint: "",
    bucket: "",
    region: "",
    prefix: "",
    accessKey: "",
    secretConfigured: false,
  },
  modelFiles: [],
  repositoryModels: [],
};

describe("instancesListReducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = instancesListReducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state).toEqual(initialInstancesListState);
  });

  it("InstancesListPageOpened_DefaultState_SetsLoadingTrue", () => {
    const state = instancesListReducer(initialInstancesListState, instancesListPageOpened());
    expect(state.loading).toBeTrue();
  });

  it("InstancesListRefreshRequested_DefaultState_SetsLoadingTrue", () => {
    const state = instancesListReducer(initialInstancesListState, instancesListRefreshRequested());
    expect(state.loading).toBeTrue();
  });

  it("InstancesListLoaded_LoadingState_SetsInstancesAndClearsLoading", () => {
    const state = instancesListReducer(
      { ...initialInstancesListState, loading: true },
      instancesListLoaded({ instances: [MOCK_INSTANCE] }),
    );
    expect(state.loading).toBeFalse();
    expect(state.instances.length).toBe(1);
    expect(state.creating).toBeFalse();
    expect(state.createError).toBeNull();
  });

  it("InstancesListLoadFailed_LoadingState_ClearsLoading", () => {
    const state = instancesListReducer(
      { ...initialInstancesListState, loading: true },
      instancesListLoadFailed({ message: "timeout" }),
    );
    expect(state.loading).toBeFalse();
  });

  it("CreateInstanceRequested_DefaultState_SetsCreatingAndClearsError", () => {
    const state = instancesListReducer(
      { ...initialInstancesListState, createError: "old" },
      createInstanceRequested({ url: "http://localhost:8001" }),
    );
    expect(state.creating).toBeTrue();
    expect(state.createError).toBeNull();
  });

  it("CreateInstanceSucceeded_CreatingState_ClearsCreating", () => {
    const state = instancesListReducer(
      { ...initialInstancesListState, creating: true },
      createInstanceSucceeded(),
    );
    expect(state.creating).toBeFalse();
  });

  it("CreateInstanceFailed_CreatingState_SetsErrorAndClearsCreating", () => {
    const state = instancesListReducer(
      { ...initialInstancesListState, creating: true },
      createInstanceFailed({ message: "already exists" }),
    );
    expect(state.creating).toBeFalse();
    expect(state.createError).toBe("already exists");
  });

  it("DeleteInstanceActions_DefaultState_TrackDeletingAndError", () => {
    let state = instancesListReducer(
      { ...initialInstancesListState, deleteError: "old" },
      deleteInstanceRequested({ instanceId: "1", instanceName: "node-1" }),
    );
    expect(state.deleting).toBeTrue();
    expect(state.deleteError).toBeNull();

    state = instancesListReducer(state, deleteInstanceSucceeded({ instanceName: "node-1" }));
    expect(state.deleting).toBeFalse();

    state = instancesListReducer(
      { ...initialInstancesListState, deleting: true },
      deleteInstanceFailed({ message: "forbidden" }),
    );
    expect(state.deleting).toBeFalse();
    expect(state.deleteError).toBe("forbidden");
  });
});
