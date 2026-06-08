import { createSelector } from "@ngrx/store";

import { instancesProfileFeature } from "./instances-profile.reducer";

export const {
  selectRunning: selectProfileRunning,
  selectActiveKey,
  selectActiveRunKey,
  selectEntries,
} = instancesProfileFeature;

export const selectActiveProfileEntry = createSelector(
  selectActiveKey,
  selectEntries,
  (activeKey, entries) => entries[activeKey] ?? { error: "", output: "", command: [] },
);

export const selectProfileError = createSelector(selectActiveProfileEntry, (entry) => entry.error);
export const selectProfileOutput = createSelector(
  selectActiveProfileEntry,
  (entry) => entry.output,
);
export const selectProfileCommand = createSelector(
  selectActiveProfileEntry,
  (entry) => entry.command,
);
