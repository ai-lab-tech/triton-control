/* eslint-disable @typescript-eslint/no-explicit-any */
import { instancesDetailReducer, initialInstancesDetailState } from "./instances-detail.reducer";
import {
  instanceDetailPageOpened,
  instanceDetailLoaded,
  instanceDetailLoadFailed,
  instanceDetailRefreshed,
  s3ConfigSaveRequested,
  s3ConfigDisableRequested,
  s3ConfigSaveSucceeded,
  s3ConfigDisableSucceeded,
} from "./instances-detail.actions";

const EMPTY_S3 = {
  enabled: false,
  endpoint: "",
  bucket: "",
  region: "",
  prefix: "",
  accessKey: "",
  secretConfigured: false,
  useHttps: false,
  verifySsl: false,
  caCertificate: "",
};

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
  s3: EMPTY_S3,
  modelFiles: [],
  repositoryModels: [],
};

describe("instancesDetailReducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = instancesDetailReducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state).toEqual(initialInstancesDetailState);
  });

  it("InstanceDetailPageOpened_DefaultState_SetsLoadingTrueAndResetsInstance", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, instance: MOCK_INSTANCE, error: "old" },
      instanceDetailPageOpened({ instanceId: "1" }),
    );
    expect(state.loading).toBeTrue();
    expect(state.instance).toBeNull();
    expect(state.error).toBeNull();
  });

  it("InstanceDetailLoaded_LoadingState_SetsInstanceAndClearsLoading", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, loading: true },
      instanceDetailLoaded({ instance: MOCK_INSTANCE }),
    );
    expect(state.loading).toBeFalse();
    expect(state.instance).toEqual(MOCK_INSTANCE);
    expect(state.error).toBeNull();
  });

  it("InstanceDetailLoadFailed_LoadingState_SetsErrorAndClearsLoading", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, loading: true },
      instanceDetailLoadFailed({ message: "not found" }),
    );
    expect(state.loading).toBeFalse();
    expect(state.instance).toBeNull();
    expect(state.error).toBe("not found");
  });

  it("InstanceDetailRefreshed_InstanceLoaded_MergesPartialUpdate", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, instance: MOCK_INSTANCE },
      instanceDetailRefreshed({ partial: { models: 5 } as any }),
    );
    expect(state.instance!.models).toBe(5);
    expect(state.instance!.name).toBe("node-1");
  });

  it("InstanceDetailRefreshed_NoInstance_LeavesInstanceNull", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, instance: null },
      instanceDetailRefreshed({ partial: { models: 5 } as any }),
    );
    expect(state.instance).toBeNull();
  });

  it("S3ConfigSaveRequested_DefaultState_SetsS3Saving", () => {
    const state = instancesDetailReducer(
      initialInstancesDetailState,
      s3ConfigSaveRequested({
        instanceId: "1",
        payload: {
          endpoint: "e",
          bucket: "bkt",
          region: "r",
          prefix: "p",
          access_key: "a",
          secret_key: "b",
        },
      }),
    );
    expect(state.s3Saving).toBeTrue();
  });

  it("S3ConfigDisableRequested_DefaultState_SetsS3Saving", () => {
    const state = instancesDetailReducer(
      initialInstancesDetailState,
      s3ConfigDisableRequested({ instanceId: "1", currentS3: MOCK_INSTANCE.s3 }),
    );
    expect(state.s3Saving).toBeTrue();
  });

  it("S3ConfigSaveSucceeded_InstanceLoaded_UpdatesS3AndClearsSaving", () => {
    const newS3 = {
      ...EMPTY_S3,
      enabled: true,
      endpoint: "e2",
      bucket: "b2",
      region: "r2",
      prefix: "p2",
    };
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, s3Saving: true, instance: MOCK_INSTANCE },
      s3ConfigSaveSucceeded({ s3: newS3 }),
    );
    expect(state.s3Saving).toBeFalse();
    expect(state.instance!.s3).toEqual(newS3);
  });

  it("S3ConfigSaveSucceeded_NoInstance_LeavesInstanceNull", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, s3Saving: true, instance: null },
      s3ConfigSaveSucceeded({ s3: EMPTY_S3 }),
    );
    expect(state.s3Saving).toBeFalse();
    expect(state.instance).toBeNull();
  });

  it("S3ConfigDisableSucceeded_InstanceLoaded_UpdatesS3AndClearsSaving", () => {
    const newS3 = EMPTY_S3;
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, s3Saving: true, instance: MOCK_INSTANCE },
      s3ConfigDisableSucceeded({ s3: newS3 }),
    );
    expect(state.s3Saving).toBeFalse();
    expect(state.instance!.s3.enabled).toBeFalse();
  });

  it("S3ConfigDisableSucceeded_NoInstance_LeavesInstanceNull", () => {
    const state = instancesDetailReducer(
      { ...initialInstancesDetailState, s3Saving: true, instance: null },
      s3ConfigDisableSucceeded({ s3: EMPTY_S3 }),
    );
    expect(state.instance).toBeNull();
  });
});
