import { Injectable, inject } from "@angular/core";
import { Router } from "@angular/router";
import { from, of } from "rxjs";
import { catchError, map, switchMap, tap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import { AuthService } from "../../shared/auth/auth.service";
import { AuthStore } from "../../shared/auth/auth.store";

import {
  bootstrapStatusFailed,
  bootstrapStatusLoaded,
  continueRequested,
  loginPageOpened,
  loginWithOidcRequested,
  loginWithPasswordFailed,
  loginWithPasswordRequested,
  loginWithPasswordSucceeded,
  registerWithPasswordFailed,
  registerWithPasswordRequested,
  registerWithPasswordSucceeded,
  sessionRefreshFailed,
  sessionRefreshed,
} from "./login.actions";

@Injectable()
export class LoginEffects {
  private readonly actions$ = inject(Actions);
  private readonly auth = inject(AuthService);
  private readonly authState = inject(AuthStore);
  private readonly router = inject(Router);

  /** Load bootstrap status (oidcEnabled + needsSetup) on page open */
  readonly loadBootstrapStatus$ = createEffect(() =>
    this.actions$.pipe(
      ofType(loginPageOpened),
      switchMap(() =>
        from(this.auth.getBootstrapStatus()).pipe(
          map(({ oidcEnabled, needsSetup }) =>
            bootstrapStatusLoaded({ oidcEnabled, needsBootstrap: !oidcEnabled && needsSetup }),
          ),
          catchError(() => of(bootstrapStatusFailed())),
        ),
      ),
    ),
  );

  /** Silently refresh session on page open — keeps isLoggedIn/accessAllowed up-to-date */
  readonly refreshSession$ = createEffect(() =>
    this.actions$.pipe(
      ofType(loginPageOpened),
      switchMap(() =>
        from(this.auth.refreshSession()).pipe(
          map(() => sessionRefreshed()),
          catchError(() => of(sessionRefreshFailed())),
        ),
      ),
    ),
  );

  /** Auto-leave login page after successful session refresh. */
  readonly navigateAfterSessionRefresh$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(sessionRefreshed),
        tap(() => {
          if (!this.authState.isLoggedIn() || !this.authState.accessAllowed()) return;
          if (!this.router.url.startsWith("/signin")) return;
          const queryIndex = this.router.url.indexOf("?");
          const search = queryIndex >= 0 ? this.router.url.substring(queryIndex + 1) : "";
          const params = new URLSearchParams(search);
          const returnUrl = this.normalizeReturnUrl(params.get("returnUrl"));
          void this.router.navigateByUrl(returnUrl, { replaceUrl: true });
        }),
      ),
    { dispatch: false },
  );

  /** Login with password (also handles first-user bootstrap registration) */
  readonly loginWithPassword$ = createEffect(() =>
    this.actions$.pipe(
      ofType(loginWithPasswordRequested),
      switchMap(({ email, password, needsBootstrap, returnUrl }) =>
        from(
          (async () => {
            if (needsBootstrap) {
              try {
                await this.auth.registerFirstUser(email, password);
              } catch {
                // Ignore race errors and proceed with login.
              }
            }
            await this.auth.loginWithPassword(email, password);
          })(),
        ).pipe(
          map(() => loginWithPasswordSucceeded({ returnUrl })),
          catchError((error: unknown) =>
            of(
              loginWithPasswordFailed({
                message: error instanceof Error ? error.message : "Invalid email or password.",
              }),
            ),
          ),
        ),
      ),
    ),
  );

  /** Navigate after successful login — check access_allowed from AuthStateService */
  readonly navigateAfterLogin$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(loginWithPasswordSucceeded),
        tap(({ returnUrl }) => {
          if (this.authState.accessAllowed()) {
            void this.router.navigateByUrl(returnUrl);
          }
          // If not accessAllowed, loginWithPasswordFailed is NOT dispatched here —
          // the "pending" banner is driven by authState.isLoggedIn + authState.accessAllowed directly.
        }),
      ),
    { dispatch: false },
  );

  /** Register a new account */
  readonly registerWithPassword$ = createEffect(() =>
    this.actions$.pipe(
      ofType(registerWithPasswordRequested),
      switchMap(({ email, password }) =>
        from(this.auth.registerWithPassword(email, password)).pipe(
          map(() => registerWithPasswordSucceeded()),
          catchError((error: unknown) =>
            of(
              registerWithPasswordFailed({
                message: error instanceof Error ? error.message : "Registration failed.",
              }),
            ),
          ),
        ),
      ),
    ),
  );

  /** Hard-redirect to OIDC provider */
  readonly loginWithOidc$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(loginWithOidcRequested),
        tap(() => this.auth.loginWithOidc()),
      ),
    { dispatch: false },
  );

  /** "Continue" button: refresh session if needed, then navigate */
  readonly continue$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(continueRequested),
        switchMap(({ returnUrl }) =>
          from(
            (async () => {
              if (!this.authState.isLoggedIn()) {
                await this.auth.refreshSession().catch(() => {
                  // ignore
                });
              }
            })(),
          ).pipe(
            tap(() => {
              if (this.authState.isLoggedIn() && this.authState.accessAllowed()) {
                void this.router.navigateByUrl(this.normalizeReturnUrl(returnUrl));
              }
            }),
            catchError(() => of(void 0)),
          ),
        ),
      ),
    { dispatch: false },
  );

  private normalizeReturnUrl(raw: string | null | undefined): string {
    const fallback = "/dashboard";
    if (!raw) return fallback;
    let value = `${raw}`.trim();
    try {
      value = decodeURIComponent(value);
    } catch {
      // keep original when decoding fails
    }
    if (!value.startsWith("/")) {
      value = `/${value}`;
    }
    return value || fallback;
  }
}
