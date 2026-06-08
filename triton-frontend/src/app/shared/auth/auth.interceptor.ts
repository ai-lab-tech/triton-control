import { HttpErrorResponse, HttpInterceptorFn } from "@angular/common/http";
import { inject } from "@angular/core";
import { Router } from "@angular/router";
import { catchError, throwError } from "rxjs";
import { BASE_PATH } from "../../api/generated/index";
import { AuthService } from "./auth.service";

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const configuredBase = inject(BASE_PATH, { optional: true }) ?? "";
  const runtimeBase = configuredBase.trim().replace(/\/$/, "");

  if (!runtimeBase || !req.url.startsWith(runtimeBase)) {
    return next(req);
  }

  const auth = inject(AuthService);
  const router = inject(Router);
  const token = auth.getAccessToken();
  const isAuthLoginLikeRequest =
    req.url.endsWith("/api/auth/login") ||
    req.url.endsWith("/api/auth/self-register") ||
    req.url.endsWith("/api/auth/bootstrap/register");
  const isAuthLogoutRequest = req.url.endsWith("/logout");

  let cloned = req.clone({ withCredentials: true });
  if (token && !cloned.headers.has("Authorization")) {
    cloned = cloned.clone({
      setHeaders: {
        Authorization: `Bearer ${token}`,
      },
    });
  }

  return next(cloned).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        error.status === 401 &&
        !isAuthLoginLikeRequest &&
        !isAuthLogoutRequest
      ) {
        auth.clearLocalSession();
        if (router.url !== "/signin") {
          void router.navigate(["/signin"], {
            queryParams: router.url && router.url !== "/" ? { returnUrl: router.url } : undefined,
          });
        }
      }
      return throwError(() => error);
    }),
  );
};
