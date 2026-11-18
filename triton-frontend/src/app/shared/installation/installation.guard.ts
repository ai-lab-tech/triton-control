import { inject } from "@angular/core";
import { CanActivateChildFn, Router } from "@angular/router";
import { InstallationService } from "./installation.service";

export const installationGuard: CanActivateChildFn = async (route) => {
  const installation = inject(InstallationService);
  const router = inject(Router);
  if (route.routeConfig?.path === "installation-restricted") return true;
  const loaded = await installation.load();
  const required = route.data["capability"] as string | undefined;
  if (loaded && (!required || installation.allows(required))) return true;
  return router.createUrlTree(["/installation-restricted"], {
    queryParams: { reason: loaded ? "production-policy" : "policy-unavailable" },
  });
};
