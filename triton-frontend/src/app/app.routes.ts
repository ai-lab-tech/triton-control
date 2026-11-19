import { EngineeringQueueComponent } from "./pages/preparation/engineering-queue.component";
import { PreparationDetailComponent } from "./pages/preparation/preparation-detail.component";
import { installationGuard } from "./shared/installation/installation.guard";
import { InstallationRestrictedComponent } from "./shared/installation/installation-restricted.component";
import { TaskBuildsPageComponent } from "./pages/builds/task-builds-page.component";
import { HandoffPageComponent } from "./pages/handoffs/handoff-page.component";
import { Routes } from "@angular/router";
import { adminGuard, authGuard } from "./shared/auth/auth.guard";
import { ShellComponent } from "./shell/shell.component";
import { DashboardPageComponent } from "./pages/dashboard/dashboard-page.component";
import { InstancesPageComponent } from "./pages/instances/instances-page.component";
import { WorkspacesPageComponent } from "./pages/workspaces/workspaces-page.component";
import { NewDeploymentPageComponent } from "./pages/deployments/new-deployment-page.component";
import { InstanceDetailPageComponent } from "./pages/instances/detail/instance-detail-page.component";
import { InstanceModelInferPageComponent } from "./pages/instances/infer/instance-model-infer-page.component";
import { InstanceModelProfilePageComponent } from "./pages/instances/profile/instance-model-profile-page.component";
import { InstanceS3BrowserPageComponent } from "./pages/instances/s3/instance-s3-browser-page.component";
import { UsersPageComponent } from "./pages/users/users-page.component";
import { SettingsPageComponent } from "./pages/settings/settings-page.component";
import { ErrorLogsPageComponent } from "./pages/error-logs/error-logs-page.component";
import { LoginPageComponent } from "./pages/login/login-page.component";
import { S3ProfilesPageComponent } from "./pages/s3-profiles/s3-profiles-page.component";
import { WorkflowsPageComponent } from "./pages/workflows/workflows-page.component";
import { MlflowPageComponent } from "./pages/mlflow/mlflow-page.component";
import { AccountLifecyclePageComponent } from "./pages/account-lifecycle/account-lifecycle-page.component";

export const APP_ROUTES: Routes = [
  { path: "signin", component: LoginPageComponent },
  { path: "forgot-password", component: AccountLifecyclePageComponent, data: { mode: "forgot" } },
  {
    path: "activate-account",
    component: AccountLifecyclePageComponent,
    data: { mode: "activate" },
  },
  {
    path: "reset-password",
    component: AccountLifecyclePageComponent,
    data: { mode: "reset" },
  },
  {
    path: "",
    component: ShellComponent,
    canActivate: [authGuard],
    canActivateChild: [installationGuard],
    children: [
      {
        path: "preparation",
        data: { capability: "development_jobs" },
        component: EngineeringQueueComponent,
      },
      {
        path: "preparation/tasks/:taskId",
        data: { capability: "development_jobs" },
        component: PreparationDetailComponent,
      },
      { path: "installation-restricted", component: InstallationRestrictedComponent },
      {
        path: "preparation/tasks/:taskId/builds",
        data: { capability: "development_jobs" },
        component: TaskBuildsPageComponent,
      },
      { path: "", pathMatch: "full", redirectTo: "dashboard" },
      { path: "dashboard", component: DashboardPageComponent },
      {
        path: "preparation/handoffs/new",
        data: { capability: "development_jobs" },
        component: HandoffPageComponent,
      },
      {
        path: "preparation/handoffs/:handoffId",
        data: { capability: "development_jobs" },
        component: HandoffPageComponent,
      },
      { path: "instances", component: InstancesPageComponent },
      {
        path: "workspaces",
        data: { capability: "development_tools" },
        component: WorkspacesPageComponent,
      },
      { path: "development", redirectTo: "workspaces", pathMatch: "full" },
      {
        path: "deployments/new",
        data: { capability: "direct_deployment" },
        component: NewDeploymentPageComponent,
      },
      {
        path: "workflows",
        data: { capability: "development_tools" },
        component: WorkflowsPageComponent,
      },
      { path: "mlflow", data: { capability: "development_tools" }, component: MlflowPageComponent },
      { path: "instances/:id", component: InstanceDetailPageComponent },
      {
        path: "instances/:id/models/:modelName/versions/:version/infer",
        data: { capability: "interactive_inference" },
        component: InstanceModelInferPageComponent,
      },
      {
        path: "instances/:id/models/:modelName/versions/:version/profile",
        data: { capability: "development_jobs" },
        component: InstanceModelProfilePageComponent,
      },
      { path: "instances/:id/s3", component: InstanceS3BrowserPageComponent },
      { path: "s3-profiles", component: S3ProfilesPageComponent },
      { path: "users", component: UsersPageComponent, canActivate: [adminGuard] },
      { path: "teams", redirectTo: "users", pathMatch: "full" },
      { path: "settings", component: SettingsPageComponent, canActivate: [adminGuard] },
      { path: "error-logs", component: ErrorLogsPageComponent, canActivate: [adminGuard] },
    ],
  },
  { path: "**", redirectTo: "signin" },
];
