import { inject } from "@angular/core";
import { CanActivateFn, GuardResult, Router } from "@angular/router";

import { AuthService } from "./auth.service";
import { AuthStore } from "./auth.store";

export const authGuard: CanActivateFn = async (_route, state) => {
  const router = inject(Router);
  const auth = inject(AuthService);
  const authState = inject(AuthStore);

  // Fast path: already authenticated in-memory
  if (authState.isLoggedIn() && authState.accessAllowed()) {
    return true;
  }

  // Try to rehydrate from backend session cookie
  try {
    await auth.refreshSession();
  } catch {
    // ignore
  }

  if (authState.isLoggedIn() && authState.accessAllowed()) {
    return true;
  }

  return router.createUrlTree(["/signin"], {
    queryParams: state.url && state.url !== "/" ? { returnUrl: state.url } : undefined,
  });
};

export const adminGuard: CanActivateFn = async (route, state) => {
  const authState = inject(AuthStore);
  const router = inject(Router);

  const authResult = await (authGuard(route, state) as Promise<GuardResult>);
  if (authResult !== true) {
    return authResult;
  }

  if (authState.isAdmin()) {
    return true;
  }

  return router.createUrlTree(["/signin"], {
    queryParams: state.url && state.url !== "/" ? { returnUrl: state.url } : undefined,
  });
};
