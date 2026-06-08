/* eslint-disable @typescript-eslint/no-explicit-any */
import { instancesS3Reducer, initialInstancesS3State } from "./instances-s3.reducer";
import {
  s3PageOpened,
  s3PageDataLoaded,
  s3PageDataLoadFailed,
  s3NavigateTo,
  s3EntriesLoaded,
  s3EntriesLoadFailed,
  s3EditorOpenRequested,
  s3EditorContentLoaded,
  s3EditorContentLoadFailed,
  s3EditorSaveSucceeded,
  s3EditorClosed,
  s3FileUploadFailed,
  s3FileUploadRequested,
  s3FileUploadSucceeded,
} from "./instances-s3.actions";

describe("instancesS3Reducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = instancesS3Reducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state).toEqual(initialInstancesS3State);
  });

  it("S3PageOpened_DefaultState_SetsPageLoadingAndResetsState", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, instanceName: "old", bucketName: "bucket" },
      s3PageOpened({ instanceId: "1" }),
    );
    expect(state.pageLoading).toBeTrue();
    expect(state.instanceName).toBe("");
    expect(state.bucketName).toBe("");
  });

  it("S3PageDataLoaded_LoadingState_SetsInstanceNameAndBucket", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, pageLoading: true },
      s3PageDataLoaded({ instanceName: "node-1", bucketName: "my-bucket" }),
    );
    expect(state.instanceName).toBe("node-1");
    expect(state.bucketName).toBe("my-bucket");
  });

  it("S3PageDataLoadFailed_LoadingState_ClearsPageLoading", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, pageLoading: true },
      s3PageDataLoadFailed({ message: "error" }),
    );
    expect(state.pageLoading).toBeFalse();
  });

  it("S3NavigateTo_DefaultState_UpdatesCurrentPath", () => {
    const state = instancesS3Reducer(
      initialInstancesS3State,
      s3NavigateTo({ instanceId: "1", path: "/models/" }),
    );
    expect(state.currentPath).toBe("/models/");
  });

  it("S3EntriesLoaded_DefaultState_UpdatesEntriesAndMergesFolderPaths", () => {
    const entries = [
      { name: "model-a", path: "/models/model-a", type: "folder" as const, modified: "" },
    ];
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, pageLoading: true },
      s3EntriesLoaded({ path: "/models/", entries, newFolderPaths: ["/models/"] }),
    );
    expect(state.entries.length).toBe(1);
    expect(state.currentPath).toBe("/models/");
    expect(state.pageLoading).toBeFalse();
    expect(state.knownFolderPaths).toContain("/models/");
  });

  it("S3EntriesLoaded_DuplicateFolderPaths_DeduplicatesPaths", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, knownFolderPaths: ["/"] },
      s3EntriesLoaded({ path: "/", entries: [], newFolderPaths: ["/"] }),
    );
    expect(state.knownFolderPaths.filter((p) => p === "/").length).toBe(1);
  });

  it("S3EntriesLoadFailed_LoadingState_ClearsPageLoading", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, pageLoading: true },
      s3EntriesLoadFailed({ message: "error" }),
    );
    expect(state.pageLoading).toBeFalse();
  });

  it("S3EditorOpenRequested_DefaultState_SetsEditorOpenAndLoading", () => {
    const state = instancesS3Reducer(
      initialInstancesS3State,
      s3EditorOpenRequested({
        instanceId: "1",
        filePath: "/models/config.json",
        fileName: "config.json",
      }),
    );
    expect(state.editorOpen).toBeTrue();
    expect(state.editorLoading).toBeTrue();
    expect(state.editorFileName).toBe("config.json");
    expect(state.editorFilePath).toBe("/models/config.json");
  });

  it("S3EditorContentLoaded_LoadingState_ClearsEditorLoading", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, editorLoading: true },
      s3EditorContentLoaded({ content: "{}" }),
    );
    expect(state.editorLoading).toBeFalse();
  });

  it("S3EditorContentLoadFailed_LoadingState_ClearsEditorLoading", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, editorLoading: true },
      s3EditorContentLoadFailed({ filePath: "/models/config.json" }),
    );
    expect(state.editorLoading).toBeFalse();
  });

  it("S3EditorSaveSucceeded_EditorOpen_ClosesEditorAndClearsFields", () => {
    const state = instancesS3Reducer(
      {
        ...initialInstancesS3State,
        editorOpen: true,
        editorFileName: "config.json",
        editorFilePath: "/models/config.json",
      },
      s3EditorSaveSucceeded({ instanceId: "1", path: "/models/", fileName: "config.json" }),
    );
    expect(state.editorOpen).toBeFalse();
    expect(state.editorFileName).toBe("");
    expect(state.editorFilePath).toBe("");
  });

  it("S3EditorClosed_EditorOpen_ClosesEditorAndClearsFields", () => {
    const state = instancesS3Reducer(
      { ...initialInstancesS3State, editorOpen: true, editorFileName: "config.json" },
      s3EditorClosed(),
    );
    expect(state.editorOpen).toBeFalse();
    expect(state.editorFileName).toBe("");
  });

  it("S3FileUploadRequested_DefaultState_SetsUploadLoadingAndFileName", () => {
    const state = instancesS3Reducer(
      initialInstancesS3State,
      s3FileUploadRequested({
        instanceId: "1",
        filePath: "/models/model.py",
        content: "x=1",
        contentType: "text/plain",
        fileName: "model.py",
        currentPath: "/models",
      }),
    );

    expect(state.uploadLoading).toBeTrue();
    expect(state.uploadFileName).toBe("model.py");
  });

  it("S3FileUploadCompleted_UploadLoadingState_ClearsUploadState", () => {
    const loadingState = {
      ...initialInstancesS3State,
      uploadLoading: true,
      uploadFileName: "model.py",
    };

    const successState = instancesS3Reducer(
      loadingState,
      s3FileUploadSucceeded({ instanceId: "1", path: "/models", fileName: "model.py" }),
    );
    const failedState = instancesS3Reducer(loadingState, s3FileUploadFailed({ message: "failed" }));

    expect(successState.uploadLoading).toBeFalse();
    expect(successState.uploadFileName).toBe("");
    expect(failedState.uploadLoading).toBeFalse();
    expect(failedState.uploadFileName).toBe("");
  });
});
