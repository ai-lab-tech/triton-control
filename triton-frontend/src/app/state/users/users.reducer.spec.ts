/* eslint-disable @typescript-eslint/no-explicit-any */
import { usersReducer } from "./users.reducer";
import {
  usersPageOpened,
  usersDataLoaded,
  usersDataLoadFailed,
  createUserSucceeded,
  deleteUserSucceeded,
  updateUserRoleSucceeded,
  updateUserRoleFailed,
  addInstanceToUserSucceeded,
  removeInstanceFromUserSucceeded,
} from "./users.actions";

const INITIAL = usersReducer(undefined, { type: "__INIT__" } as any);

const MOCK_USER: any = {
  id: 1,
  name: "Alice",
  email: "alice@example.com",
  role: "viewer",
  isActive: true,
  auth: "local",
  instances: [],
};

describe("usersReducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = usersReducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state.users).toEqual([]);
    expect(state.loading).toBeFalse();
  });

  it("UsersPageOpened_DefaultState_SetsLoadingTrue", () => {
    const state = usersReducer(INITIAL, usersPageOpened());
    expect(state.loading).toBeTrue();
    expect(state.error).toBe("");
  });

  it("UsersDataLoaded_LoadingState_SetsUsersAndClearsLoading", () => {
    const state = usersReducer(
      { ...INITIAL, loading: true },
      usersDataLoaded({ users: [MOCK_USER], instances: ["inst-1"], oidcEnabled: true }),
    );
    expect(state.loading).toBeFalse();
    expect(state.users.length).toBe(1);
    expect(state.instances).toEqual(["inst-1"]);
    expect(state.oidcEnabled).toBeTrue();
  });

  it("UsersDataLoadFailed_LoadingState_SetsErrorAndClearsLoading", () => {
    const state = usersReducer(
      { ...INITIAL, loading: true },
      usersDataLoadFailed({ message: "server error" }),
    );
    expect(state.loading).toBeFalse();
    expect(state.error).toBe("server error");
  });

  it("CreateUserSucceeded_ExistingUsers_PrependsNewUser", () => {
    const state = usersReducer(
      { ...INITIAL, users: [MOCK_USER] },
      createUserSucceeded({ user: { ...MOCK_USER, id: 2, name: "Bob" } }),
    );
    expect(state.users.length).toBe(2);
    expect(state.users[0].name).toBe("Bob");
  });

  it("DeleteUserSucceeded_UserExists_RemovesUser", () => {
    const state = usersReducer(
      { ...INITIAL, users: [MOCK_USER, { ...MOCK_USER, id: 2, name: "Bob" }] },
      deleteUserSucceeded({ userId: 1, email: "alice@example.com" }),
    );
    expect(state.users.length).toBe(1);
    expect(state.users[0].id).toBe(2);
  });

  it("UpdateUserRoleSucceeded_UserExists_UpdatesRoleAndSetsActive", () => {
    const state = usersReducer(
      { ...INITIAL, users: [{ ...MOCK_USER, isActive: false }] },
      updateUserRoleSucceeded({ userId: 1, role: "admin" }),
    );
    expect(state.users[0].role).toBe("admin");
    expect(state.users[0].isActive).toBeTrue();
  });

  it("UpdateUserRoleFailed_DefaultState_SetsError", () => {
    const state = usersReducer(
      INITIAL,
      updateUserRoleFailed({ userId: 1, prevRole: "viewer", message: "permission denied" }),
    );
    expect(state.error).toBe("permission denied");
  });

  it("AddInstanceToUserSucceeded_UserExists_UpdatesInstances", () => {
    const state = usersReducer(
      { ...INITIAL, users: [MOCK_USER] },
      addInstanceToUserSucceeded({ userId: 1, instances: ["inst-1", "inst-2"] }),
    );
    expect(state.users[0].instances).toEqual(["inst-1", "inst-2"]);
  });

  it("RemoveInstanceFromUserSucceeded_UserExists_UpdatesInstances", () => {
    const state = usersReducer(
      { ...INITIAL, users: [{ ...MOCK_USER, instances: ["inst-1", "inst-2"] }] },
      removeInstanceFromUserSucceeded({ userId: 1, instances: ["inst-1"] }),
    );
    expect(state.users[0].instances).toEqual(["inst-1"]);
  });
});
