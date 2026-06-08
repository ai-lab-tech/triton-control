import "zone.js";
import "@angular/compiler";
import { provideZoneChangeDetection } from "@angular/core";
import { provideHttpClient, withInterceptors } from "@angular/common/http";
import { MatSnackBarModule } from "@angular/material/snack-bar";
import { importProvidersFrom } from "@angular/core";

import { bootstrapApplication } from "@angular/platform-browser";
import { provideAnimationsAsync } from "@angular/platform-browser/animations/async";
import { provideRouter, withInMemoryScrolling } from "@angular/router";
import { provideEffects } from "@ngrx/effects";
import { provideState, provideStore } from "@ngrx/store";

import { AppComponent } from "./app/app.component";
import { APP_ROUTES } from "./app/app.routes";
import { authInterceptor } from "./app/shared/auth/auth.interceptor";
import { BASE_PATH, Configuration } from "./app/api/generated/index";
import { environment } from "./environments/environment";
import { InstancesInferEffects } from "./app/state/instances-infer/instances-infer.effects";
import { instancesInferFeature } from "./app/state/instances-infer/instances-infer.reducer";
import { InstancesProfileEffects } from "./app/state/instances-profile/instances-profile.effects";
import { instancesProfileFeature } from "./app/state/instances-profile/instances-profile.reducer";
import { InstancesDetailEffects } from "./app/state/instances-detail/instances-detail.effects";
import { instancesDetailFeature } from "./app/state/instances-detail/instances-detail.reducer";
import { InstancesListEffects } from "./app/state/instances-list/instances-list.effects";
import { instancesListFeature } from "./app/state/instances-list/instances-list.reducer";
import { InstancesS3Effects } from "./app/state/instances-s3/instances-s3.effects";
import { instancesS3Feature } from "./app/state/instances-s3/instances-s3.reducer";
import { SharedEffects } from "./app/state/shared/shared.effects";
import { LoginEffects } from "./app/state/login/login.effects";
import { LOGIN_FEATURE_KEY, loginReducer } from "./app/state/login/login.reducer";
import { SettingsEffects } from "./app/state/settings/settings.effects";
import { SETTINGS_FEATURE_KEY, settingsReducer } from "./app/state/settings/settings.reducer";
import { UsersEffects } from "./app/state/users/users.effects";
import { USERS_FEATURE_KEY, usersReducer } from "./app/state/users/users.reducer";
import { DashboardEffects } from "./app/state/dashboard/dashboard.effects";
import { DASHBOARD_FEATURE_KEY, dashboardReducer } from "./app/state/dashboard/dashboard.reducer";

const configuredBasePath = `${environment.apiBaseUrl ?? ""}`.trim().replace(/\/$/, "");
const runtimeBasePath = configuredBasePath || window.location.origin;

bootstrapApplication(AppComponent, {
  providers: [
    provideZoneChangeDetection(),
    provideAnimationsAsync(),
    importProvidersFrom(MatSnackBarModule),
    provideRouter(APP_ROUTES, withInMemoryScrolling({ scrollPositionRestoration: "top" })),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideStore(),
    provideState(instancesInferFeature),
    provideState(instancesProfileFeature),
    provideState(instancesDetailFeature),
    provideState(instancesListFeature),
    provideState(instancesS3Feature),
    provideState(LOGIN_FEATURE_KEY, loginReducer),
    provideState(SETTINGS_FEATURE_KEY, settingsReducer),
    provideState(USERS_FEATURE_KEY, usersReducer),
    provideState(DASHBOARD_FEATURE_KEY, dashboardReducer),
    provideEffects([
      InstancesInferEffects,
      InstancesProfileEffects,
      InstancesDetailEffects,
      InstancesListEffects,
      InstancesS3Effects,
      LoginEffects,
      SettingsEffects,
      UsersEffects,
      DashboardEffects,
      SharedEffects,
    ]),
    { provide: BASE_PATH, useValue: runtimeBasePath },
    { provide: Configuration, useFactory: () => new Configuration({ withCredentials: true }) },
  ],
}).catch((err: unknown) => console.error(err));
