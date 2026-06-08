import { createAction, props } from "@ngrx/store";

import { type BrowserEntry } from "./instances-s3.reducer";

export const s3PageOpened = createAction(
  "[Instances S3] Page Opened",
  props<{ instanceId: string }>(),
);

export const s3PageDataLoaded = createAction(
  "[Instances S3] Page Data Loaded",
  props<{ instanceName: string; bucketName: string }>(),
);

export const s3PageDataLoadFailed = createAction(
  "[Instances S3] Page Data Load Failed",
  props<{ message: string }>(),
);

export const s3NavigateTo = createAction(
  "[Instances S3] Navigate To",
  props<{ instanceId: string; path: string }>(),
);

export const s3EntriesLoaded = createAction(
  "[Instances S3] Entries Loaded",
  props<{ path: string; entries: BrowserEntry[]; newFolderPaths: string[] }>(),
);

export const s3EntriesLoadFailed = createAction(
  "[Instances S3] Entries Load Failed",
  props<{ message: string }>(),
);

export const s3EditorOpenRequested = createAction(
  "[Instances S3] Editor Open Requested",
  props<{ instanceId: string; filePath: string; fileName: string }>(),
);

export const s3EditorContentLoaded = createAction(
  "[Instances S3] Editor Content Loaded",
  props<{ content: string }>(),
);

export const s3EditorContentLoadFailed = createAction(
  "[Instances S3] Editor Content Load Failed",
  props<{ filePath: string }>(),
);

export const s3EditorSaveRequested = createAction(
  "[Instances S3] Editor Save Requested",
  props<{ instanceId: string; filePath: string; content: string }>(),
);

export const s3EditorSaveSucceeded = createAction(
  "[Instances S3] Editor Save Succeeded",
  props<{ instanceId: string; path: string; fileName: string }>(),
);

export const s3EditorSaveFailed = createAction(
  "[Instances S3] Editor Save Failed",
  props<{ message: string }>(),
);

export const s3EditorClosed = createAction("[Instances S3] Editor Closed");

export const s3FileUploadRequested = createAction(
  "[Instances S3] File Upload Requested",
  props<{
    instanceId: string;
    filePath: string;
    content: string | ArrayBuffer;
    contentType: string;
    fileName: string;
    currentPath: string;
  }>(),
);

export const s3FileUploadSucceeded = createAction(
  "[Instances S3] File Upload Succeeded",
  props<{ instanceId: string; path: string; fileName: string }>(),
);

export const s3FileUploadFailed = createAction(
  "[Instances S3] File Upload Failed",
  props<{ message: string }>(),
);
