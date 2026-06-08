import { createFeature, createReducer, on } from "@ngrx/store";

import {
  s3EditorClosed,
  s3EditorContentLoadFailed,
  s3EditorContentLoaded,
  s3EditorOpenRequested,
  s3EditorSaveSucceeded,
  s3EntriesLoadFailed,
  s3EntriesLoaded,
  s3FileUploadFailed,
  s3FileUploadRequested,
  s3FileUploadSucceeded,
  s3NavigateTo,
  s3PageDataLoadFailed,
  s3PageDataLoaded,
  s3PageOpened,
} from "./instances-s3.actions";

export const INSTANCES_S3_FEATURE_KEY = "instancesS3";

export type BrowserEntry = {
  name: string;
  path: string;
  type: "folder" | "file";
  size?: string;
  modified: string;
};

export interface InstancesS3State {
  instanceName: string;
  bucketName: string;
  currentPath: string;
  entries: BrowserEntry[];
  knownFolderPaths: string[];
  pageLoading: boolean;
  editorOpen: boolean;
  editorLoading: boolean;
  editorFileName: string;
  editorFilePath: string;
  uploadLoading: boolean;
  uploadFileName: string;
}

export const initialInstancesS3State: InstancesS3State = {
  instanceName: "",
  bucketName: "",
  currentPath: "/",
  entries: [],
  knownFolderPaths: ["/"],
  pageLoading: false,
  editorOpen: false,
  editorLoading: false,
  editorFileName: "",
  editorFilePath: "",
  uploadLoading: false,
  uploadFileName: "",
};

export const instancesS3Reducer = createReducer(
  initialInstancesS3State,
  on(s3PageOpened, () => ({ ...initialInstancesS3State, pageLoading: true })),
  on(s3PageDataLoaded, (state, { instanceName, bucketName }) => ({
    ...state,
    instanceName,
    bucketName,
  })),
  on(s3PageDataLoadFailed, (state) => ({ ...state, pageLoading: false })),
  on(s3NavigateTo, (state, { path }) => ({ ...state, currentPath: path })),
  on(s3EntriesLoaded, (state, { path, entries, newFolderPaths }) => ({
    ...state,
    currentPath: path,
    entries,
    knownFolderPaths: [...new Set([...state.knownFolderPaths, ...newFolderPaths])],
    pageLoading: false,
  })),
  on(s3EntriesLoadFailed, (state) => ({ ...state, pageLoading: false })),
  on(s3EditorOpenRequested, (state, { filePath, fileName }) => ({
    ...state,
    editorOpen: true,
    editorLoading: true,
    editorFileName: fileName,
    editorFilePath: filePath,
  })),
  on(s3EditorContentLoaded, (state) => ({ ...state, editorLoading: false })),
  on(s3EditorContentLoadFailed, (state) => ({ ...state, editorLoading: false })),
  on(s3EditorSaveSucceeded, (state) => ({
    ...state,
    editorOpen: false,
    editorLoading: false,
    editorFileName: "",
    editorFilePath: "",
  })),
  on(s3EditorClosed, (state) => ({
    ...state,
    editorOpen: false,
    editorLoading: false,
    editorFileName: "",
    editorFilePath: "",
  })),
  on(s3FileUploadRequested, (state, { fileName }) => ({
    ...state,
    uploadLoading: true,
    uploadFileName: fileName,
  })),
  on(s3FileUploadSucceeded, s3FileUploadFailed, (state) => ({
    ...state,
    uploadLoading: false,
    uploadFileName: "",
  })),
);

export const instancesS3Feature = createFeature({
  name: INSTANCES_S3_FEATURE_KEY,
  reducer: instancesS3Reducer,
});
