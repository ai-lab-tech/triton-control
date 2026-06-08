import { Injectable, inject } from "@angular/core";
import { of } from "rxjs";
import { catchError, exhaustMap, map, mergeMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import { PerfAnalyzersService } from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { displayFailure } from "../shared/shared.actions";
import {
  profileLastResultLoadSkipped,
  profileLastResultLoadStarted,
  profileLastResultLoadSucceeded,
  profileRunFailed,
  profileRunStarted,
  profileRunSucceeded,
} from "./instances-profile.actions";

@Injectable()
export class InstancesProfileEffects {
  private readonly actions$ = inject(Actions);
  private readonly perfAnalyzersApi = inject(PerfAnalyzersService);

  readonly loadLastProfileResult$ = createEffect(() =>
    this.actions$.pipe(
      ofType(profileLastResultLoadStarted),
      mergeMap((action) =>
        this.perfAnalyzersApi
          .getLatestPerfAnalyzerRunApiPerfAnalyzersRunsLatestGet(
            Number(action.instanceId),
            action.modelName,
            action.version,
          )
          .pipe(
            map((response) => {
              if (!response.found) {
                return profileLastResultLoadSkipped({ key: action.key });
              }
              return profileLastResultLoadSucceeded({
                key: action.key,
                batchSize: response.batch_size,
                concurrencyRange: response.concurrency_range,
                measurementRequestCount: response.measurement_request_count,
                inputData: response.input_data,
                command: Array.isArray(response.command) ? response.command : [],
                output: response.output || "",
              });
            }),
            catchError(() => of(profileLastResultLoadSkipped({ key: action.key }))),
          ),
      ),
    ),
  );

  readonly runProfile$ = createEffect(() =>
    this.actions$.pipe(
      ofType(profileRunStarted),
      exhaustMap((action) =>
        this.perfAnalyzersApi
          .runPerfAnalyzerApiPerfAnalyzersRunsPost({
            instance_id: Number(action.instanceId),
            model_name: action.modelName,
            model_version: action.version,
            batch_size: action.batchSize,
            concurrency_range: action.concurrencyRange,
            measurement_request_count: action.measurementRequestCount,
            input_data: action.inputData,
          })
          .pipe(
            map((response) =>
              profileRunSucceeded({
                key: action.key,
                command: Array.isArray(response.command) ? response.command : [],
                output: response.output || "Profiler completed without output.",
              }),
            ),
            catchError((error) =>
              of(
                profileRunFailed({
                  key: action.key,
                  message: mapApiErrorMessage(error, "Profiler run failed."),
                }),
              ),
            ),
          ),
      ),
    ),
  );

  readonly profileFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(profileRunFailed),
      mergeMap((action) =>
        of(
          displayFailure({
            title: "Profiler failed",
            message: action.message,
          }),
        ),
      ),
    ),
  );
}
