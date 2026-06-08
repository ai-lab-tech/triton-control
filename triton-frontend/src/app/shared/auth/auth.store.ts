import { computed } from "@angular/core";
import { patchState, signalStore, withComputed, withMethods, withState } from "@ngrx/signals";

type AuthState = {
  userName: string;
  userEmail: string;
  role: string;
  authProvider: "local" | "oidc" | "";
  isAdmin: boolean;
  accessAllowed: boolean;
  accessToken: string;
};

const initialState: AuthState = {
  userName: "",
  userEmail: "",
  role: "",
  authProvider: "",
  isAdmin: false,
  accessAllowed: false,
  accessToken: "",
};

export const AuthStore = signalStore(
  { providedIn: "root" },
  withState(initialState),
  withComputed((store) => ({
    isLoggedIn: computed(() => store.userName().trim().length > 0),
    canWriteInstances: computed(() =>
      ["admin", "member"].includes(store.role().trim().toLowerCase()),
    ),
  })),
  withMethods((store) => ({
    setAuthenticatedUser(payload: {
      name: string;
      email?: string;
      role?: string;
      authProvider?: "local" | "oidc";
      accessAllowed?: boolean;
      accessToken?: string;
    }) {
      const patch: Partial<AuthState> = {
        userName: payload.name,
        userEmail: payload.email ?? "",
        role: payload.role ?? "User",
        authProvider: payload.authProvider ?? "local",
        isAdmin: (payload.role ?? "").toLowerCase() === "admin",
        accessAllowed: payload.accessAllowed ?? true,
      };
      if (payload.accessToken !== undefined) {
        patch.accessToken = payload.accessToken;
      }
      patchState(store, patch);
    },

    setAccessToken(token: string): void {
      patchState(store, { accessToken: token });
    },

    logout(): void {
      patchState(store, {
        userName: "",
        userEmail: "",
        role: "",
        authProvider: "" as AuthState["authProvider"],
        isAdmin: false,
        accessAllowed: false,
        accessToken: "",
      });
    },
  })),
);
