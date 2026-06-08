import { Injectable, inject } from "@angular/core";
import { of, forkJoin } from "rxjs";
import { catchError, map, mergeMap, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import {
  InstanceS3ConfigDTO,
  InstancesService,
  S3EntryDTO,
  S3FileContentResponse,
  S3ListResponse,
  TritonInstanceDTO,
} from "../../api/generated/index";
import { type BrowserEntry } from "./instances-s3.reducer";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { displayFailure, displaySuccess } from "../shared/shared.actions";
import {
  s3EditorClosed,
  s3EditorContentLoadFailed,
  s3EditorContentLoaded,
  s3EditorOpenRequested,
  s3EditorSaveFailed,
  s3EditorSaveRequested,
  s3EditorSaveSucceeded,
  s3EntriesLoadFailed,
  s3EntriesLoaded,
  s3FileUploadFailed,
  s3FileUploadRequested,
  s3FileUploadSucceeded,
  s3NavigateTo,
  s3PageDataLoadFailed,
  s3PageDataLoaded,
  s3PageOpened,
} from "./instances-s3.actions";

@Injectable()
export class InstancesS3Effects {
  private readonly actions$ = inject(Actions);
  private readonly instancesApi = inject(InstancesService);

  readonly loadPageData$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3PageOpened),
      switchMap(({ instanceId }) =>
        forkJoin([
          this.instancesApi
            .getInstanceApiInstancesInstanceIdGet(instanceId)
            .pipe(catchError(() => of(null))),
          this.instancesApi
            .getInstanceS3ApiInstancesInstanceIdS3Get(instanceId)
            .pipe(catchError(() => of(null))),
        ]).pipe(
          mergeMap(([instance, s3Raw]) => {
            const dto = instance as TritonInstanceDTO | null;
            const s3 = s3Raw as InstanceS3ConfigDTO | null;
            return of(
              s3PageDataLoaded({
                instanceName: dto?.name ?? "",
                bucketName: s3?.bucket ?? "",
              }),
              s3NavigateTo({ instanceId, path: "/" }),
            );
          }),
          catchError((error) =>
            of(
              s3PageDataLoadFailed({
                message: mapApiErrorMessage(error, "Failed to load S3 browser data."),
              }),
            ),
          ),
        ),
      ),
    ),
  );

  readonly navigateTo$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3NavigateTo),
      switchMap(({ instanceId, path }) => {
        const clean = path.replace(/^\/+|\/+$/g, "");
        const prefix = clean ? `${clean}/` : "";
        return this.instancesApi
          .listInstanceS3ApiInstancesInstanceIdS3ListGet(instanceId, prefix)
          .pipe(
            map((response) => {
              const rawEntries = ((response as S3ListResponse)?.entries ?? []) as S3EntryDTO[];
              const entries: BrowserEntry[] = rawEntries.map((entry) => ({
                name: entry.name,
                path: normalizePath(entry.path) || path,
                type: entry.type,
                size: entry.size ? `${Math.ceil(entry.size / 1024)} KB` : undefined,
                modified: entry.modified
                  ? new Date(String(entry.modified)).toISOString().slice(0, 10)
                  : "",
              }));
              const newFolderPaths = computeNewFolderPaths(path, entries);
              return s3EntriesLoaded({ path, entries, newFolderPaths });
            }),
            catchError((error) =>
              of(
                s3EntriesLoadFailed({
                  message: mapApiErrorMessage(error, "Failed to load S3 entries."),
                }),
              ),
            ),
          );
      }),
    ),
  );

  readonly openEditor$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3EditorOpenRequested),
      switchMap(({ instanceId, filePath }) =>
        this.instancesApi
          .getInstanceS3ContentApiInstancesInstanceIdS3ContentGet(instanceId, filePath)
          .pipe(
            map((response) => {
              const content = (response as S3FileContentResponse)?.content ?? "";
              return s3EditorContentLoaded({ content });
            }),
            catchError(() => of(s3EditorContentLoadFailed({ filePath }))),
          ),
      ),
    ),
  );

  readonly saveEditor$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3EditorSaveRequested),
      switchMap(({ instanceId, filePath, content }) => {
        const parts = normalizePath(filePath).split("/").filter(Boolean);
        const fileName = parts[parts.length - 1] ?? "";
        parts.pop();
        const path = parts.length ? `/${parts.join("/")}` : "/";
        return this.instancesApi
          .putInstanceS3ContentApiInstancesInstanceIdS3ContentPut(
            content,
            filePath,
            instanceId,
            "text/plain; charset=utf-8",
          )
          .pipe(
            mergeMap(() =>
              of(
                s3EditorSaveSucceeded({ instanceId, path, fileName }),
                s3NavigateTo({ instanceId, path }),
              ),
            ),
            catchError((error) =>
              of(
                s3EditorSaveFailed({
                  message: mapApiErrorMessage(error, `Failed to save file ${fileName}.`),
                }),
              ),
            ),
          );
      }),
    ),
  );

  readonly uploadFile$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3FileUploadRequested),
      switchMap(({ instanceId, filePath, content, contentType, fileName, currentPath }) =>
        this.instancesApi
          .putInstanceS3ContentApiInstancesInstanceIdS3ContentPut(
            content,
            filePath,
            instanceId,
            contentType || "application/octet-stream",
          )
          .pipe(
            mergeMap(() =>
              of(
                s3FileUploadSucceeded({ instanceId, path: currentPath, fileName }),
                s3NavigateTo({ instanceId, path: currentPath }),
              ),
            ),
            catchError((error) =>
              of(
                s3FileUploadFailed({
                  message: mapApiErrorMessage(error, `Failed to upload file ${fileName}.`),
                }),
              ),
            ),
          ),
      ),
    ),
  );

  // --- Toasts ---

  readonly saveSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3EditorSaveSucceeded),
      mergeMap(({ fileName }) =>
        of(displaySuccess({ message: `Saved ${fileName} successfully.` })),
      ),
    ),
  );

  readonly saveFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3EditorSaveFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "Save failed", message }))),
    ),
  );

  readonly uploadSuccessToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3FileUploadSucceeded),
      mergeMap(({ fileName }) =>
        of(displaySuccess({ message: `Uploaded ${fileName} successfully.` })),
      ),
    ),
  );

  readonly uploadFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3FileUploadFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "Upload failed", message }))),
    ),
  );

  readonly pageDataFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3PageDataLoadFailed),
      mergeMap(({ message }) => of(displayFailure({ title: "S3 browser error", message }))),
    ),
  );

  readonly editorContentFailureToast$ = createEffect(() =>
    this.actions$.pipe(
      ofType(s3EditorContentLoadFailed),
      mergeMap(({ filePath }) =>
        of(
          displayFailure({
            title: "File load failed",
            message: `Failed to load file content for ${filePath}.`,
          }),
        ),
      ),
    ),
  );

  // Unused — kept for completeness
  readonly s3EditorClosedEffect$ = createEffect(() => this.actions$.pipe(ofType(s3EditorClosed)), {
    dispatch: false,
  });
}

// --- Pure helpers (module-level) ---

function normalizePath(path: string | null | undefined): string {
  const clean = `${path ?? ""}`.replace(/\/+/g, "/").replace(/^\/+|\/+$/g, "");
  return clean ? `/${clean}` : "/";
}

function joinPath(basePath: string, name: string): string {
  const base = normalizePath(basePath);
  const cleanName = `${name ?? ""}`.replace(/^\/+|\/+$/g, "");
  if (!cleanName) {
    return base;
  }
  return normalizePath(`${base}/${cleanName}`);
}

function collectAncestorPaths(path: string): string[] {
  const normalized = normalizePath(path);
  const paths: string[] = ["/"];
  const parts = normalized.split("/").filter(Boolean);
  let acc = "";
  for (const part of parts) {
    acc += `/${part}`;
    paths.push(acc);
  }
  return paths;
}

function computeNewFolderPaths(basePath: string, entries: BrowserEntry[]): string[] {
  const paths = collectAncestorPaths(basePath);
  for (const entry of entries) {
    if (entry.type === "folder") {
      paths.push(...collectAncestorPaths(joinPath(basePath, entry.name)));
    }
  }
  return [...new Set(paths)];
}
