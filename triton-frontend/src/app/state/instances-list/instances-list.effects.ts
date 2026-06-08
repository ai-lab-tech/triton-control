import { Injectable, inject } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { of } from "rxjs";
import { catchError, map, mergeMap, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import {
  BASE_PATH,
  Configuration,
  CreateTritonInstanceRequest,
  DeploymentsService,
  InstancesService,
} from "../../api/generated/index";
import { dtoToInstance } from "../instances.utils";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { displayFailure, displaySuccess } from "../shared/shared.actions";
import {
  createInstanceFailed,
  createInstanceRequested,
  createInstanceSucceeded,
  deleteInstanceFailed,
  deleteInstanceRequested,
  deleteInstanceSucceeded,
  instancesListLoadFailed,
  instancesListLoaded,
  instancesListPageOpened,
  instancesListRefreshRequested,
} from "./instances-list.actions";

@Injectable()
export class InstancesListEffects {
  private readonly actions$ = inject(Actions);
  private readonly instancesApi = inject(InstancesService);
  private readonly deploymentsApi = inject(DeploymentsService);
  private readonly http = inject(HttpClient);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");
  private readonly configuration = inject(Configuration, { optional: true });

  readonly loadList$ = createEffect(() =>
    this.actions$.pipe(
      ofType(instancesListPageOpened, instancesListRefreshRequested),
      switchMap(() =>
        this.instancesApi.listInstancesApiInstancesGet().pipe(
          map((rows) =>
            instancesListLoaded({
              instances: Array.isArray(rows) ? rows.map(dtoToInstance) : [],
            }),
          ),
          catchError((error) =>
            of(
              instancesListLoadFailed({
                message: mapApiErrorMessage(error, "Failed to load instances."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly createInstance$ = createEffect(() =>
    this.actions$.pipe(
      ofType(createInstanceRequested),
      switchMap(({ name, url, verifySsl, caCertificate, metricsUrl }) => {
        const payload: CreateTritonInstanceRequest = {
          url,
          name: name || undefined,
          verify_ssl: !!verifySsl,
          ca_certificate: verifySsl ? (caCertificate ?? "") : "",
          metrics_url: metricsUrl?.trim() || undefined,
        };
        return this.instancesApi.createInstanceApiInstancesPost(payload).pipe(
          mergeMap(() => of(createInstanceSucceeded(), instancesListRefreshRequested())),
          catchError((error) =>
            of(
              createInstanceFailed({
                message: mapApiErrorMessage(error, "Failed to create instance."),
              }),
            ),
          ),
        );
      }),
    ),
  );

  readonly createSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(createInstanceSucceeded),
      mergeMap(() => of(displaySuccess({ message: "Instance added successfully." }))),
    ),
  );

  readonly createFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(createInstanceFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "Failed to add instance", message }))),
    ),
  );

  readonly deleteInstance$ = createEffect(() =>
    this.actions$.pipe(
      ofType(deleteInstanceRequested),
      switchMap(({ instanceId, instanceName, isSelfDeployed }) =>
        (isSelfDeployed
          ? this.deploymentsApi.deleteDeploymentApiDeploymentsInstanceIdDelete(instanceId)
          : this.http.delete(this.instanceUrl(instanceId), {
              withCredentials: this.configuration?.withCredentials,
            })
        ).pipe(
          mergeMap(() =>
            of(deleteInstanceSucceeded({ instanceName }), instancesListRefreshRequested()),
          ),
          catchError((error) =>
            of(
              deleteInstanceFailed({
                message: mapApiErrorMessage(error, "Failed to delete instance."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  private instanceUrl(instanceId: string): string {
    return `${this.basePath}/api/instances/${encodeURIComponent(String(instanceId))}`;
  }

  readonly deleteSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(deleteInstanceSucceeded),
      mergeMap(({ instanceName }) =>
        of(displaySuccess({ message: `Instance ${instanceName} deleted successfully.` })),
      ),
    ),
  );

  readonly deleteFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(deleteInstanceFailed),
      mergeMap(({ message }) =>
        of(displayFailure({ title: "Failed to delete instance", message })),
      ),
    ),
  );
}
