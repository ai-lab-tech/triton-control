import { instancesListFeature } from "./instances-list.reducer";

export const {
  selectInstances,
  selectLoading: selectInstancesListLoading,
  selectCreating: selectInstancesCreating,
  selectCreateError: selectInstancesCreateError,
} = instancesListFeature;
