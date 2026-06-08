import { createFeature, createReducer, on } from "@ngrx/store";
import {
  bootstrapStatusFailed,
  bootstrapStatusLoaded,
  continueRequested,
  loginPageOpened,
  loginWithPasswordFailed,
  loginWithPasswordRequested,
  loginWithPasswordSucceeded,
  registerModeDisabled,
  registerModeEnabled,
  registerWithPasswordFailed,
  registerWithPasswordRequested,
  registerWithPasswordSucceeded,
} from "./login.actions";

export const LOGIN_FEATURE_KEY = "login";

export interface LoginState {
  oidcEnabled: boolean;
  needsBootstrap: boolean;
  loading: boolean;
  error: string;
  notice: string;
  registerMode: boolean;
}

const initialState: LoginState = {
  oidcEnabled: false,
  needsBootstrap: false,
  loading: false,
  error: "",
  notice: "",
  registerMode: false,
};

export const loginReducer = createReducer(
  initialState,

  on(loginPageOpened, (state) => ({ ...state, error: "", notice: "" })),

  on(bootstrapStatusLoaded, (state, { oidcEnabled, needsBootstrap }) => ({
    ...state,
    oidcEnabled,
    needsBootstrap,
  })),

  on(bootstrapStatusFailed, (state) => ({
    ...state,
    // Avoid stale OIDC UI state when bootstrap-status request fails.
    oidcEnabled: false,
    needsBootstrap: false,
  })),

  on(loginWithPasswordRequested, (state) => ({ ...state, loading: true, error: "", notice: "" })),

  on(loginWithPasswordSucceeded, (state) => ({
    ...state,
    loading: false,
    error: "",
    registerMode: false,
  })),

  on(loginWithPasswordFailed, (state, { message }) => ({
    ...state,
    loading: false,
    error: message,
  })),

  on(registerWithPasswordRequested, (state) => ({
    ...state,
    loading: true,
    error: "",
    notice: "",
  })),

  on(registerWithPasswordSucceeded, (state) => ({
    ...state,
    loading: false,
    registerMode: false,
    notice: "Account created. Please log in with your email and password.",
  })),

  on(registerWithPasswordFailed, (state, { message }) => ({
    ...state,
    loading: false,
    error: message,
  })),

  on(continueRequested, (state) => ({ ...state, error: "" })),

  on(registerModeEnabled, (state) => ({ ...state, registerMode: true, error: "", notice: "" })),

  on(registerModeDisabled, (state) => ({ ...state, registerMode: false })),
);

export const loginFeature = createFeature({
  name: LOGIN_FEATURE_KEY,
  reducer: loginReducer,
});
