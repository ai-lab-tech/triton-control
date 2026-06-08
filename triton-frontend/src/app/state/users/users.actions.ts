import { createAction, props } from "@ngrx/store";
import { type UserRow } from "./users.reducer";

export const usersPageOpened = createAction("[Users] Page Opened");

export const usersDataLoaded = createAction(
  "[Users] Data Loaded",
  props<{ users: UserRow[]; instances: string[]; oidcEnabled: boolean }>(),
);

export const usersDataLoadFailed = createAction(
  "[Users] Data Load Failed",
  props<{ message: string }>(),
);

export const createUserRequested = createAction(
  "[Users] Create User Requested",
  props<{
    name: string;
    email: string;
    role: string;
    auth: "local" | "oidc";
    password?: string;
    instances: string[];
  }>(),
);

export const createUserSucceeded = createAction(
  "[Users] Create User Succeeded",
  props<{ user: UserRow }>(),
);

export const createUserFailed = createAction(
  "[Users] Create User Failed",
  props<{ message: string }>(),
);

export const deleteUserRequested = createAction(
  "[Users] Delete User Requested",
  props<{ userId: number; email: string }>(),
);

export const deleteUserSucceeded = createAction(
  "[Users] Delete User Succeeded",
  props<{ userId: number; email: string }>(),
);

export const deleteUserFailed = createAction(
  "[Users] Delete User Failed",
  props<{ message: string }>(),
);

export const updateUserRoleRequested = createAction(
  "[Users] Update User Role Requested",
  props<{ userId: number; role: string; prevRole: string }>(),
);

export const updateUserRoleSucceeded = createAction(
  "[Users] Update User Role Succeeded",
  props<{ userId: number; role: string }>(),
);

export const updateUserRoleFailed = createAction(
  "[Users] Update User Role Failed",
  props<{ userId: number; prevRole: string; message: string }>(),
);

export const addInstanceToUserRequested = createAction(
  "[Users] Add Instance To User Requested",
  props<{ userId: number; instances: string[] }>(),
);

export const addInstanceToUserSucceeded = createAction(
  "[Users] Add Instance To User Succeeded",
  props<{ userId: number; instances: string[] }>(),
);

export const removeInstanceFromUserRequested = createAction(
  "[Users] Remove Instance From User Requested",
  props<{ userId: number; instances: string[] }>(),
);

export const removeInstanceFromUserSucceeded = createAction(
  "[Users] Remove Instance From User Succeeded",
  props<{ userId: number; instances: string[] }>(),
);
