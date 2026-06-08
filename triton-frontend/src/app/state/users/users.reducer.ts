import { createFeature, createReducer, on } from "@ngrx/store";
import {
  addInstanceToUserSucceeded,
  createUserSucceeded,
  deleteUserSucceeded,
  removeInstanceFromUserSucceeded,
  updateUserRoleFailed,
  updateUserRoleSucceeded,
  usersDataLoadFailed,
  usersDataLoaded,
  usersPageOpened,
} from "./users.actions";

export const USERS_FEATURE_KEY = "users";

export interface UserRow {
  id: number;
  name: string;
  email: string;
  role: string;
  isActive: boolean;
  auth: "local" | "oidc";
  instances: string[];
}

export interface UsersState {
  users: UserRow[];
  instances: string[];
  oidcEnabled: boolean;
  loading: boolean;
  error: string;
}

const initialState: UsersState = {
  users: [],
  instances: [],
  oidcEnabled: false,
  loading: false,
  error: "",
};

export const usersReducer = createReducer(
  initialState,

  on(usersPageOpened, (state) => ({ ...state, loading: true, error: "" })),

  on(usersDataLoaded, (state, { users, instances, oidcEnabled }) => ({
    ...state,
    loading: false,
    users,
    instances,
    oidcEnabled,
  })),

  on(usersDataLoadFailed, (state, { message }) => ({
    ...state,
    loading: false,
    error: message,
  })),

  on(createUserSucceeded, (state, { user }) => ({
    ...state,
    users: [user, ...state.users],
  })),

  on(deleteUserSucceeded, (state, { userId }) => ({
    ...state,
    users: state.users.filter((u) => u.id !== userId),
  })),

  on(updateUserRoleSucceeded, (state, { userId, role }) => ({
    ...state,
    users: state.users.map((u) => (u.id === userId ? { ...u, role, isActive: true } : u)),
  })),

  on(updateUserRoleFailed, (state, { message }) => ({
    ...state,
    error: message,
  })),

  on(addInstanceToUserSucceeded, (state, { userId, instances }) => ({
    ...state,
    users: state.users.map((u) => (u.id === userId ? { ...u, instances } : u)),
  })),

  on(removeInstanceFromUserSucceeded, (state, { userId, instances }) => ({
    ...state,
    users: state.users.map((u) => (u.id === userId ? { ...u, instances } : u)),
  })),
);

export const usersFeature = createFeature({
  name: USERS_FEATURE_KEY,
  reducer: usersReducer,
});
