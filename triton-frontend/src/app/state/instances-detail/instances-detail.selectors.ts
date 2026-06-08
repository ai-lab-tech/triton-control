import { instancesDetailFeature } from "./instances-detail.reducer";

export const {
  selectInstance: selectDetailInstance,
  selectLoading: selectDetailLoading,
  selectError: selectDetailError,
  selectS3Saving: selectDetailS3Saving,
  selectTritonSaving: selectDetailTritonSaving,
} = instancesDetailFeature;
