import { Injectable, inject } from "@angular/core";
import { TimeoutError, of } from "rxjs";
import { catchError, exhaustMap, map, mergeMap, timeout } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import { InstancesService } from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { environment } from "../../../environments/environment";
import { displayFailure } from "../shared/shared.actions";
import {
  type InferenceMetrics,
  inferRequestFailed,
  inferRequestStarted,
  inferRequestSucceeded,
} from "./instances-infer.actions";

@Injectable()
export class InstancesInferEffects {
  private readonly actions$ = inject(Actions);
  private readonly instancesApi = inject(InstancesService);
  private readonly inferenceTimeoutMs = environment.inferenceRequestTimeoutMs;

  readonly runInference$ = createEffect(() =>
    this.actions$.pipe(
      ofType(inferRequestStarted),
      exhaustMap((action) => {
        const requestStartedAt = performance.now();
        return this.instancesApi
          .inferInstanceModelApiInstancesInstanceIdModelsModelNameVersionsVersionInferPost(
            action.payload,
            action.instanceId,
            action.modelName,
            action.version,
            "response",
          )
          .pipe(
            timeout(this.inferenceTimeoutMs),
            map((response) =>
              inferRequestSucceeded({
                response: response.body ?? {},
                requestLatencyMs: performance.now() - requestStartedAt,
                inferenceMetrics: this.decodeInferenceMetrics(
                  response.headers.get("X-Triton-Inference-Metrics"),
                ),
              }),
            ),
            catchError((error) => {
              const message =
                error instanceof TimeoutError
                  ? `Inference request timed out after ${Math.round(this.inferenceTimeoutMs / 1000)}s.`
                  : mapApiErrorMessage(error, "Inference request failed.");

              return of(
                inferRequestFailed({
                  message,
                  requestLatencyMs: performance.now() - requestStartedAt,
                }),
              );
            }),
          );
      }),
    ),
  );

  readonly inferFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(inferRequestFailed),
      mergeMap((action) =>
        of(
          displayFailure({
            title: "Inference failed",
            message: action.message,
          }),
        ),
      ),
    ),
  );

  private decodeInferenceMetrics(value: string | null): InferenceMetrics | null {
    if (!value) {
      return null;
    }

    try {
      const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
      const padded = normalized.padEnd(
        normalized.length + ((4 - (normalized.length % 4)) % 4),
        "=",
      );
      const parsed = JSON.parse(atob(padded)) as InferenceMetrics;
      if (!parsed || typeof parsed.available !== "boolean" || !Array.isArray(parsed.models)) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }
}
