import { Injectable, inject } from "@angular/core";
import { from, of } from "rxjs";
import { catchError, map, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import { AuthService } from "../../shared/auth/auth.service";
import { environment } from "../../../environments/environment";
import {
  oidcSettingsLoadFailed,
  oidcSettingsLoaded,
  oidcSettingsSaveFailed,
  oidcSettingsSaveRedirecting,
  oidcSettingsSaveRequested,
  oidcSettingsSaveSucceeded,
  settingsPageOpened,
} from "./settings.actions";

@Injectable()
export class SettingsEffects {
  private readonly actions$ = inject(Actions);
  private readonly auth = inject(AuthService);

  readonly loadSettings$ = createEffect(() =>
    this.actions$.pipe(
      ofType(settingsPageOpened),
      switchMap(() =>
        from(this.auth.getOidcSettings()).pipe(
          map((raw) => oidcSettingsLoaded({ settings: withRequiredSettings(raw) })),
          catchError((error: unknown) =>
            of(
              oidcSettingsLoadFailed({
                message: errorText(error, "Failed to load settings."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly saveSettings$ = createEffect(() =>
    this.actions$.pipe(
      ofType(oidcSettingsSaveRequested),
      switchMap(({ settings }) =>
        from(this.auth.saveOidcSettings(settings)).pipe(
          map((savedResponse) => {
            if (!savedResponse) {
              return oidcSettingsSaveRedirecting();
            }
            return oidcSettingsSaveSucceeded({ settings: withRequiredSettings(savedResponse) });
          }),
          catchError((error: unknown) =>
            of(
              oidcSettingsSaveFailed({
                message: errorText(error, "Failed to save settings."),
              }),
            ),
          ),
        ),
      ),
    ),
  );
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const text = `${error.message ?? ""}`.trim();
    if (text) return text;
  }
  return fallback;
}

function withRequiredSettings(
  settings: ReturnType<AuthService["getOidcSettings"]> extends Promise<infer T> ? T : never,
) {
  const apiBaseUrl = `${settings?.apiBaseUrl ?? ""}`.trim() || environment.apiBaseUrl;
  return {
    ...settings,
    caCertificate: settings?.caCertificate ?? "",
    configSource: settings?.configSource ?? "db",
    kubernetesEnabled: !!settings?.kubernetesEnabled,
    apiBaseUrl,
  };
}
