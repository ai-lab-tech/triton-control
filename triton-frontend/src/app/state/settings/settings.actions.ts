import { createAction, props } from "@ngrx/store";
import { type OidcSettings } from "../../shared/auth/auth.service";

export const settingsPageOpened = createAction("[Settings] Page Opened");

export const oidcSettingsLoaded = createAction(
  "[Settings] OIDC Settings Loaded",
  props<{ settings: OidcSettings }>(),
);

export const oidcSettingsLoadFailed = createAction(
  "[Settings] OIDC Settings Load Failed",
  props<{ message: string }>(),
);

export const oidcSettingsSaveRequested = createAction(
  "[Settings] OIDC Settings Save Requested",
  props<{ settings: OidcSettings }>(),
);

export const oidcSettingsSaveSucceeded = createAction(
  "[Settings] OIDC Settings Save Succeeded",
  props<{ settings: OidcSettings }>(),
);

export const oidcSettingsSaveRedirecting = createAction(
  "[Settings] OIDC Settings Save Redirecting",
);

export const oidcSettingsSaveFailed = createAction(
  "[Settings] OIDC Settings Save Failed",
  props<{ message: string }>(),
);
