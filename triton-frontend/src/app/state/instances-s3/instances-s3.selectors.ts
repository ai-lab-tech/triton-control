import { instancesS3Feature } from "./instances-s3.reducer";

export const {
  selectInstanceName: selectS3InstanceName,
  selectBucketName: selectS3BucketName,
  selectCurrentPath: selectS3CurrentPath,
  selectEntries: selectS3Entries,
  selectKnownFolderPaths: selectS3KnownFolderPaths,
  selectPageLoading: selectS3PageLoading,
  selectEditorOpen: selectS3EditorOpen,
  selectEditorLoading: selectS3EditorLoading,
  selectEditorFileName: selectS3EditorFileName,
  selectEditorFilePath: selectS3EditorFilePath,
  selectUploadLoading: selectS3UploadLoading,
  selectUploadFileName: selectS3UploadFileName,
} = instancesS3Feature;
