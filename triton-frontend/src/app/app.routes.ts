import { Routes } from "@angular/router";
import { adminGuard, authGuard } from "./shared/auth/auth.guard";
import { ShellComponent } from "./shell/shell.component";
import { DashboardPageComponent } from "./pages/dashboard/dashboard-page.component";
import { InstancesPageComponent } from "./pages/instances/instances-page.component";
import { DevelopmentPageComponent } from "./pages/development/development-page.component";
import { NewDeploymentPageComponent } from "./pages/deployments/new-deployment-page.component";
import { NewPerfAnalyzerPageComponent } from "./pages/perf-analyzers/new-perf-analyzer-page.component";
import { InstanceDetailPageComponent } from "./pages/instances/detail/instance-detail-page.component";
import { InstanceModelInferPageComponent } from "./pages/instances/infer/instance-model-infer-page.component";
import { InstanceModelProfilePageComponent } from "./pages/instances/profile/instance-model-profile-page.component";
import { InstanceS3BrowserPageComponent } from "./pages/instances/s3/instance-s3-browser-page.component";
import { UsersPageComponent } from "./pages/users/users-page.component";
import { SettingsPageComponent } from "./pages/settings/settings-page.component";
import { LoginPageComponent } from "./pages/login/login-page.component";

export const APP_ROUTES: Routes = [
  { path: "signin", component: LoginPageComponent },
  {
    path: "",
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: "", pathMatch: "full", redirectTo: "dashboard" },
      { path: "dashboard", component: DashboardPageComponent },
      { path: "instances", component: InstancesPageComponent },
      { path: "development", component: DevelopmentPageComponent },
      { path: "deployments/new", component: NewDeploymentPageComponent },
      { path: "perf-analyzers", component: NewPerfAnalyzerPageComponent },
      { path: "instances/:id", component: InstanceDetailPageComponent },
      {
        path: "instances/:id/models/:modelName/versions/:version/infer",
        component: InstanceModelInferPageComponent,
      },
      {
        path: "instances/:id/models/:modelName/versions/:version/profile",
        component: InstanceModelProfilePageComponent,
      },
      { path: "instances/:id/s3", component: InstanceS3BrowserPageComponent },
      { path: "users", component: UsersPageComponent, canActivate: [adminGuard] },
      { path: "settings", component: SettingsPageComponent, canActivate: [adminGuard] },
    ],
  },
  { path: "**", redirectTo: "signin" },
];
