import { createFeature, createReducer, on } from "@ngrx/store";
import {
  oidcSettingsLoadFailed,
  oidcSettingsLoaded,
  oidcSettingsSaveFailed,
  oidcSettingsSaveRedirecting,
  oidcSettingsSaveRequested,
  oidcSettingsSaveSucceeded,
  settingsPageOpened,
} from "./settings.actions";

export const SETTINGS_FEATURE_KEY = "settings";

export type MessageTone = "info" | "success" | "error";

export interface SettingsState {
  loading: boolean;
  saving: boolean;
  message: string;
  messageTone: MessageTone;
}

const initialState: SettingsState = {
  loading: false,
  saving: false,
  message: "",
  messageTone: "info",
};

export const settingsReducer = createReducer(
  initialState,

  on(settingsPageOpened, (state) => ({
    ...state,
    loading: true,
    message: "",
    messageTone: "info" as MessageTone,
  })),

  on(oidcSettingsLoaded, (state) => ({ ...state, loading: false })),

  on(oidcSettingsLoadFailed, (state, { message }) => ({
    ...state,
    loading: false,
    message,
    messageTone: "error" as MessageTone,
  })),

  on(oidcSettingsSaveRequested, (state) => ({
    ...state,
    saving: true,
    message: "",
    messageTone: "info" as MessageTone,
  })),

  on(oidcSettingsSaveSucceeded, (state) => ({
    ...state,
    saving: false,
    message: "Settings saved.",
    messageTone: "success" as MessageTone,
  })),

  on(oidcSettingsSaveRedirecting, (state) => ({
    ...state,
    saving: false,
    message: "Redirecting to OIDC login...",
    messageTone: "info" as MessageTone,
  })),

  on(oidcSettingsSaveFailed, (state, { message }) => ({
    ...state,
    saving: false,
    message,
    messageTone: "error" as MessageTone,
  })),
);

export const settingsFeature = createFeature({
  name: SETTINGS_FEATURE_KEY,
  reducer: settingsReducer,
});
