import { Injectable, inject } from "@angular/core";
import { concat, of, forkJoin } from "rxjs";
import { catchError, map, mergeMap, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import {
  InstanceS3ConfigDTO,
  InstancesService,
  TritonInstanceDTO,
  UpdateInstanceS3Request,
  UserDTO,
  UsersService,
} from "../../api/generated/index";
import { type Instance, type InstanceAssignedUser } from "../../pages/instances/instances.data";
import { dtoToInstance, resolveStatus, resolveVersion } from "../instances.utils";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { displayFailure, displaySuccess } from "../shared/shared.actions";
import {
  instanceDetailLoadFailed,
  instanceDetailLoaded,
  instanceDetailPageOpened,
  instanceDetailRefreshRequested,
  instanceDetailRefreshed,
  instanceDetailSupplementLoaded,
  s3ConfigDisableFailed,
  s3ConfigDisableRequested,
  s3ConfigDisableSucceeded,
  s3ConfigSaveFailed,
  s3ConfigSaveRequested,
  s3ConfigSaveSucceeded,
  instanceDetailRefreshSilentlyFailed,
  tritonConnectionSaveFailed,
  tritonConnectionSaveRequested,
  tritonConnectionSaveSucceeded,
} from "./instances-detail.actions";

@Injectable()
export class InstancesDetailEffects {
  private readonly actions$ = inject(Actions);
  private readonly instancesApi = inject(InstancesService);
  private readonly usersApi = inject(UsersService);

  readonly loadDetail$ = createEffect(() =>
    this.actions$.pipe(
      ofType(instanceDetailPageOpened),
      switchMap(({ instanceId }) =>
        this.instancesApi.getInstanceApiInstancesInstanceIdGet(instanceId).pipe(
          switchMap((dto) => {
            const typedDto = dto as TritonInstanceDTO;
            const baseInstance = dtoToInstance(typedDto);
            return concat(
              of(instanceDetailLoaded({ instance: baseInstance })),
              forkJoin([
                this.fetchAssignedUsers(dto.name ?? ""),
                this.instancesApi
                  .getInstanceS3ApiInstancesInstanceIdS3Get(dto.id ?? instanceId)
                  .pipe(catchError(() => of(null))),
              ]).pipe(
                map(([assignedUsers, s3Raw]) => {
                  const s3 = s3Raw as InstanceS3ConfigDTO | null;
                  return instanceDetailSupplementLoaded({
                    partial: {
                      assignedUsers,
                      s3: s3ConfigToInstanceS3(s3),
                    },
                  });
                }),
                catchError(() => of(instanceDetailRefreshSilentlyFailed())),
              ),
            );
          }),
          catchError((error) =>
            of(
              instanceDetailLoadFailed({
                message: mapApiErrorMessage(error, "Failed to load instance."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly refreshDetail$ = createEffect(() =>
    this.actions$.pipe(
      ofType(instanceDetailRefreshRequested),
      mergeMap(({ instanceId }) =>
        this.instancesApi.getInstanceApiInstancesInstanceIdGet(instanceId).pipe(
          map((dto) =>
            instanceDetailRefreshed({
              partial: this.dtoToConnectionPartial(dto as TritonInstanceDTO),
            }),
          ),
          catchError(() => of(instanceDetailRefreshSilentlyFailed())),
        ),
      ),
    ),
  );

  readonly saveS3$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3ConfigSaveRequested),
      switchMap(({ instanceId, payload }) =>
        this.instancesApi.updateInstanceS3ApiInstancesInstanceIdS3Put(payload, instanceId).pipe(
          map((updated) => {
            const s3 = updated as InstanceS3ConfigDTO;
            return s3ConfigSaveSucceeded({
              s3: s3ConfigToInstanceS3(s3),
            });
          }),
          catchError((error) =>
            of(
              s3ConfigSaveFailed({
                message: mapApiErrorMessage(error, "Failed to save S3 settings."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly disableS3$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3ConfigDisableRequested),
      switchMap(({ instanceId, currentS3 }) => {
        const payload: UpdateInstanceS3Request = {
          enabled: false,
          endpoint: currentS3.endpoint,
          bucket: currentS3.bucket,
          region: currentS3.region,
          prefix: currentS3.prefix,
          use_https: false,
          verify_ssl: false,
          address_style: "path",
        };
        return this.instancesApi
          .updateInstanceS3ApiInstancesInstanceIdS3Put(payload, instanceId)
          .pipe(
            map((updated) => {
              const s3 = updated as InstanceS3ConfigDTO;
              return s3ConfigDisableSucceeded({
                s3: s3ConfigToInstanceS3(s3),
              });
            }),
            catchError((error) =>
              of(
                s3ConfigDisableFailed({
                  message: mapApiErrorMessage(error, "Failed to disable S3 connection."),
                }),
              ),
            ),
          );
      }),
    ),
  );

  readonly saveTritonConnection$ = createEffect(() =>
    this.actions$.pipe(
      ofType(tritonConnectionSaveRequested),
      switchMap(({ instanceId, payload }) =>
        this.instancesApi.updateInstanceApiInstancesInstanceIdPut(payload, instanceId).pipe(
          map((dto) =>
            tritonConnectionSaveSucceeded({
              partial: this.dtoToConnectionPartial(dto as TritonInstanceDTO),
            }),
          ),
          catchError((error) =>
            of(
              tritonConnectionSaveFailed({
                message: mapApiErrorMessage(error, "Failed to save Triton connection."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly s3SaveSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3ConfigSaveSucceeded),
      mergeMap(() => of(displaySuccess({ message: "S3 settings saved." }))),
    ),
  );

  readonly s3DisableSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3ConfigDisableSucceeded),
      mergeMap(() => of(displaySuccess({ message: "S3 connection disabled." }))),
    ),
  );

  readonly s3FailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3ConfigSaveFailed, s3ConfigDisableFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "S3 error", message }))),
    ),
  );

  readonly tritonConnectionSaveSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(tritonConnectionSaveSucceeded),
      mergeMap(() => of(displaySuccess({ message: "Triton connection saved." }))),
    ),
  );

  readonly tritonConnectionSaveFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(tritonConnectionSaveFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "Triton connection error", message }))),
    ),
  );

  readonly loadFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(instanceDetailLoadFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "Failed to load instance", message }))),
    ),
  );

  private fetchAssignedUsers(instanceName: string) {
    if (!instanceName) {
      return of<InstanceAssignedUser[]>([]);
    }
    return this.usersApi.listUsersApiAuthUsersGet().pipe(
      map((rows) => {
        const users = rows as UserDTO[];
        return users
          .filter((row) => (row.assigned_instances ?? []).includes(instanceName))
          .map((row) => ({
            name: row.name ?? row.email ?? "",
            role: normalizeRoleLabel(row.role),
          }))
          .filter((user) => user.name.length > 0);
      }),
      catchError(() => of<InstanceAssignedUser[]>([])),
    );
  }

  private dtoToConnectionPartial(dto: TritonInstanceDTO): Partial<Instance> {
    const instance = dtoToInstance(dto);
    return {
      name: dto.name,
      url: dto.url,
      status: resolveStatus(dto),
      version: resolveVersion(dto),
      models: (dto.model_names ?? []).length,
      healthLive: !!dto.health_live,
      healthReady: !!dto.health_ready,
      healthLastCheckedAt: dto.health_last_checked_at ? String(dto.health_last_checked_at) : "",
      healthError: dto.health_error ? String(dto.health_error) : "",
      tritonVerifySsl: !!dto.triton_verify_ssl,
      tritonCaCertificate: dto.triton_ca_certificate ?? "",
      metricsUrl: dto.metrics_url ?? "",
      metricsLastCheckedAt: dto.metrics_last_checked_at ? String(dto.metrics_last_checked_at) : "",
      metricsError: dto.metrics_error ? String(dto.metrics_error) : "",
      deploymentRuntime: stringDtoField(dto, "deployment_runtime"),
      deploymentNamespace: stringDtoField(dto, "deployment_namespace"),
      deploymentName: stringDtoField(dto, "deployment_name"),
      deploymentServiceName: stringDtoField(dto, "deployment_service_name"),
      deploymentSecretName: stringDtoField(dto, "deployment_secret_name"),
      deploymentLog: stringDtoField(dto, "deployment_log"),
      isSelfDeployed: !!(dto as TritonInstanceDTO & Record<string, unknown>)["is_self_deployed"],
      podStatuses: Array.isArray(
        (dto as TritonInstanceDTO & Record<string, unknown>)["pod_statuses"],
      )
        ? ((dto as TritonInstanceDTO & Record<string, unknown>)["pod_statuses"] as string[])
        : [],
      cpu: normalizePercent(dto.metrics_cpu),
      ram: normalizePercent(dto.metrics_ram),
      gpu: normalizePercent(dto.metrics_gpu),
      serverMetadata: (dto.server_metadata ?? null) as Record<string, unknown> | null,
      repositoryModels: instance.repositoryModels,
    };
  }
}

// --- Pure helpers ---

function normalizeRoleLabel(role: unknown): string {
  const value = String(role ?? "")
    .trim()
    .toLowerCase();
  if (value === "admin") return "Admin";
  if (value === "member") return "Member";
  if (value === "viewer") return "View only";
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "View only";
}

function normalizePercent(value: unknown): number {
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(parsed * 10) / 10));
}

function stringDtoField(dto: TritonInstanceDTO, key: string): string {
  const value = (dto as TritonInstanceDTO & Record<string, unknown>)[key];
  return typeof value === "string" ? value : "";
}

type S3ConfigWithCredentialMetadata = InstanceS3ConfigDTO & {
  access_key?: unknown;
  secret_configured?: unknown;
};

function s3ConfigToInstanceS3(s3: InstanceS3ConfigDTO | null | undefined): Instance["s3"] {
  const config = s3 as S3ConfigWithCredentialMetadata | null | undefined;
  return {
    enabled: !!config?.enabled,
    bucket: config?.bucket ?? "",
    region: config?.region ?? "",
    endpoint: config?.endpoint ?? "",
    prefix: config?.prefix ?? "",
    accessKey: typeof config?.access_key === "string" ? config.access_key : "",
    secretConfigured: !!config?.secret_configured,
    useHttps: !!config?.use_https,
    verifySsl: !!config?.verify_ssl,
    caCertificate: config?.ca_certificate ?? "",
  };
}
