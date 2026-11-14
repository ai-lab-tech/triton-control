import { instancesDetailFeature } from "./instances-detail.reducer";

export const {
  selectInstance: selectDetailInstance,
  selectLoading: selectDetailLoading,
  selectError: selectDetailError,
  selectErrorStatus: selectDetailErrorStatus,
  selectS3Saving: selectDetailS3Saving,
  selectTritonSaving: selectDetailTritonSaving,
} = instancesDetailFeature;
