import { createAction, props } from "@ngrx/store";

export const loginPageOpened = createAction("[Login] Page Opened");

export const bootstrapStatusLoaded = createAction(
  "[Login] Bootstrap Status Loaded",
  props<{ oidcEnabled: boolean; needsBootstrap: boolean }>(),
);

export const bootstrapStatusFailed = createAction("[Login] Bootstrap Status Failed");

export const sessionRefreshed = createAction("[Login] Session Refreshed");

export const sessionRefreshFailed = createAction("[Login] Session Refresh Failed");

export const loginWithPasswordRequested = createAction(
  "[Login] Login With Password Requested",
  props<{ email: string; password: string; needsBootstrap: boolean; returnUrl: string }>(),
);

export const loginWithPasswordSucceeded = createAction(
  "[Login] Login With Password Succeeded",
  props<{ returnUrl: string }>(),
);

export const loginWithPasswordFailed = createAction(
  "[Login] Login With Password Failed",
  props<{ message: string }>(),
);

export const registerWithPasswordRequested = createAction(
  "[Login] Register With Password Requested",
  props<{ email: string; password: string }>(),
);

export const registerWithPasswordSucceeded = createAction(
  "[Login] Register With Password Succeeded",
);

export const registerWithPasswordFailed = createAction(
  "[Login] Register With Password Failed",
  props<{ message: string }>(),
);

export const loginWithOidcRequested = createAction("[Login] Login With OIDC Requested");

export const continueRequested = createAction(
  "[Login] Continue Requested",
  props<{ returnUrl: string }>(),
);

export const registerModeEnabled = createAction("[Login] Register Mode Enabled");

export const registerModeDisabled = createAction("[Login] Register Mode Disabled");
