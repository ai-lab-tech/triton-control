import { Injectable, inject } from "@angular/core";
import { forkJoin, from, of } from "rxjs";
import { catchError, map, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import {
  CreateUserRequest,
  InstancesService,
  TritonInstanceDTO,
  UpdateUserInstancesRequest,
  UpdateUserRoleRequest,
  UserDTO,
  UsersService,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { displayFailure } from "../shared/shared.actions";
import { type UserRow } from "./users.reducer";
import {
  addInstanceToUserRequested,
  addInstanceToUserSucceeded,
  createUserFailed,
  createUserRequested,
  createUserSucceeded,
  deleteUserFailed,
  deleteUserRequested,
  deleteUserSucceeded,
  removeInstanceFromUserRequested,
  removeInstanceFromUserSucceeded,
  updateUserRoleFailed,
  updateUserRoleRequested,
  updateUserRoleSucceeded,
  usersDataLoadFailed,
  usersDataLoaded,
  usersPageOpened,
} from "./users.actions";

@Injectable()
export class UsersEffects {
  private readonly actions$ = inject(Actions);
  private readonly usersApi = inject(UsersService);
  private readonly instancesApi = inject(InstancesService);

  readonly loadData$ = createEffect(() =>
    this.actions$.pipe(
      ofType(usersPageOpened),
      switchMap(() =>
        forkJoin({
          users: from(this.usersApi.listUsersApiAuthUsersGet()),
          authOptions: from(this.usersApi.authOptionsEndpointApiAuthOptionsGet()),
          instances: from(
            this.instancesApi
              .listInstancesApiInstancesGet()
              .pipe(catchError(() => of([] as TritonInstanceDTO[]))),
          ),
        }).pipe(
          map(({ users, authOptions, instances }) => {
            const userRows = (users as UserDTO[]).map(toUserRow);
            const instanceNames = (instances as TritonInstanceDTO[])
              .map((i) => i.name ?? "")
              .filter((n) => n.length > 0);
            const oidcEnabled = !!(authOptions as { oidc_enabled?: boolean })?.oidc_enabled;
            return usersDataLoaded({ users: userRows, instances: instanceNames, oidcEnabled });
          }),
          catchError(() => of(usersDataLoadFailed({ message: "Failed to load users." }))),
        ),
      ),
    ),
  );

  readonly createUser$ = createEffect(() =>
    this.actions$.pipe(
      ofType(createUserRequested),
      switchMap(({ name, email, role, auth, password, instances }) => {
        const payload: CreateUserRequest = {
          name,
          email,
          role,
          auth_provider: auth,
          password,
          assigned_instances: instances,
        };
        return from(this.usersApi.registerUserEndpointApiAuthRegisterPost(payload)).pipe(
          map((created) => createUserSucceeded({ user: toUserRow(created as UserDTO) })),
          catchError((error) =>
            of(
              createUserFailed({
                message: mapApiErrorMessage(error, "Failed to create user."),
              }),
            ),
          ),
        );
      }),
    ),
  );

  readonly deleteUser$ = createEffect(() =>
    this.actions$.pipe(
      ofType(deleteUserRequested),
      switchMap(({ userId, email }) =>
        from(this.usersApi.deleteUserApiAuthUsersUserIdDelete(userId)).pipe(
          map(() => deleteUserSucceeded({ userId, email })),
          catchError(() => of(deleteUserFailed({ message: "Failed to delete user." }))),
        ),
      ),
    ),
  );

  readonly updateRole$ = createEffect(() =>
    this.actions$.pipe(
      ofType(updateUserRoleRequested),
      switchMap(({ userId, role, prevRole }) => {
        const payload: UpdateUserRoleRequest = { role };
        return from(this.usersApi.updateUserRoleApiAuthUsersUserIdRolePut(payload, userId)).pipe(
          map(() => updateUserRoleSucceeded({ userId, role })),
          catchError(() =>
            of(
              updateUserRoleFailed({
                userId,
                prevRole,
                message: "Failed to update user role.",
              }),
            ),
          ),
        );
      }),
    ),
  );

  readonly addInstance$ = createEffect(() =>
    this.actions$.pipe(
      ofType(addInstanceToUserRequested),
      switchMap(({ userId, instances }) => {
        const payload: UpdateUserInstancesRequest = { assigned_instances: instances };
        return from(
          this.usersApi.updateUserInstancesApiAuthUsersUserIdInstancesPut(payload, userId),
        ).pipe(
          map(() => addInstanceToUserSucceeded({ userId, instances })),
          catchError(() =>
            of(displayFailure({ title: "Error", message: "Failed to add instance." })),
          ),
        );
      }),
    ),
  );

  readonly removeInstance$ = createEffect(() =>
    this.actions$.pipe(
      ofType(removeInstanceFromUserRequested),
      switchMap(({ userId, instances }) => {
        const payload: UpdateUserInstancesRequest = { assigned_instances: instances };
        return from(
          this.usersApi.updateUserInstancesApiAuthUsersUserIdInstancesPut(payload, userId),
        ).pipe(
          map(() => removeInstanceFromUserSucceeded({ userId, instances })),
          catchError(() =>
            of(displayFailure({ title: "Error", message: "Failed to remove instance." })),
          ),
        );
      }),
    ),
  );

  // Toast effects
  readonly onCreateFailed$ = createEffect(() =>
    this.actions$.pipe(
      ofType(createUserFailed),
      map(({ message }) => displayFailure({ title: "Create user failed", message })),
    ),
  );

  readonly onDeleteFailed$ = createEffect(() =>
    this.actions$.pipe(
      ofType(deleteUserFailed),
      map(({ message }) => displayFailure({ title: "Delete user failed", message })),
    ),
  );
}

// ---- pure helpers ----

function normalizeRole(role: string | undefined): string {
  const value = (role ?? "").trim().toLowerCase();
  if (value === "admin") return "admin";
  if (value === "viewer") return "viewer";
  if (value === "member") return "member";
  if (value === "ml engineer" || value === "sre" || value === "user") return "member";
  return "viewer";
}

function toUserRow(row: UserDTO): UserRow {
  return {
    id: row.id ?? 0,
    name: row.name ?? "",
    email: row.email ?? "",
    role: normalizeRole(row.role),
    isActive: row.is_active ?? false,
    auth: (row.auth_provider ?? "local") as "local" | "oidc",
    instances: row.assigned_instances ?? [],
  };
}
