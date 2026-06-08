import { createFeature, createReducer, on } from "@ngrx/store";
import {
  profileLastResultLoadSkipped,
  profileLastResultLoadSucceeded,
  profilePageOpened,
  profileRunFailed,
  profileRunStarted,
  profileRunSucceeded,
} from "./instances-profile.actions";

export const INSTANCES_PROFILE_FEATURE_KEY = "instancesProfile";

export interface InstancesProfileState {
  running: boolean;
  activeKey: string;
  activeRunKey: string;
  entries: Record<string, InstancesProfileEntry>;
}

export interface InstancesProfileEntry {
  error: string;
  output: string;
  command: string[];
  batchSize?: number;
  concurrencyRange?: string;
  measurementRequestCount?: number;
  inputData?: string;
}

export const initialInstancesProfileState: InstancesProfileState = {
  running: false,
  activeKey: "",
  activeRunKey: "",
  entries: {},
};

export const instancesProfileReducer = createReducer(
  initialInstancesProfileState,
  on(profilePageOpened, (state, { key }) => ({
    ...state,
    activeKey: key,
  })),
  on(
    profileLastResultLoadSucceeded,
    (
      state,
      { key, command, output, batchSize, concurrencyRange, measurementRequestCount, inputData },
    ) => ({
      ...state,
      activeKey: key,
      entries: {
        ...state.entries,
        [key]: {
          error: "",
          output,
          command,
          batchSize,
          concurrencyRange,
          measurementRequestCount,
          inputData,
        },
      },
    }),
  ),
  on(profileLastResultLoadSkipped, (state, { key }) => {
    return {
      ...state,
      activeKey: key,
      entries: {
        ...state.entries,
        [key]: {
          error: "",
          output: "",
          command: [],
        },
      },
    };
  }),
  on(profileRunStarted, (state, { key }) => ({
    ...state,
    running: true,
    activeKey: key,
    activeRunKey: key,
    entries: {
      ...state.entries,
      [key]: {
        error: "",
        output: "",
        command: [],
      },
    },
  })),
  on(profileRunSucceeded, (state, { key, command, output }) => ({
    ...state,
    running: false,
    activeRunKey: "",
    entries: {
      ...state.entries,
      [key]: {
        error: "",
        output,
        command,
      },
    },
  })),
  on(profileRunFailed, (state, { key, message }) => ({
    ...state,
    running: false,
    activeRunKey: "",
    entries: {
      ...state.entries,
      [key]: {
        error: message,
        output: "",
        command: [],
      },
    },
  })),
);

export const instancesProfileFeature = createFeature({
  name: INSTANCES_PROFILE_FEATURE_KEY,
  reducer: instancesProfileReducer,
});
