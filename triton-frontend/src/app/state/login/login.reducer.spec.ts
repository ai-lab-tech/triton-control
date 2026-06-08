import {
  bootstrapStatusFailed,
  bootstrapStatusLoaded,
  loginPageOpened,
  loginWithPasswordFailed,
  loginWithPasswordRequested,
  loginWithPasswordSucceeded,
  registerModeDisabled,
  registerModeEnabled,
  registerWithPasswordFailed,
  registerWithPasswordRequested,
  registerWithPasswordSucceeded,
} from "./login.actions";
import { loginFeature, loginReducer, LoginState } from "./login.reducer";

describe("loginReducer", () => {
  const initial = loginReducer(undefined, { type: "@@init" } as never);

  it("LoginPageOpened_StaleMessagesExist_ClearsErrorAndNotice", () => {
    const state = { ...initial, error: "bad", notice: "old" };
    const next = loginReducer(state, loginPageOpened());
    expect(next.error).toBe("");
    expect(next.notice).toBe("");
  });

  it("BootstrapStatusLoaded_OidcDisabledAndNeedsSetup_StoresFlags", () => {
    const next = loginReducer(
      initial,
      bootstrapStatusLoaded({ oidcEnabled: false, needsBootstrap: true }),
    );
    expect(next.oidcEnabled).toBeFalse();
    expect(next.needsBootstrap).toBeTrue();
  });

  it("BootstrapStatusFailed_PreviousFlagsSet_ResetsOidcAndBootstrapFlags", () => {
    const state = { ...initial, oidcEnabled: true, needsBootstrap: true };
    const next = loginReducer(state, bootstrapStatusFailed());
    expect(next.oidcEnabled).toBeFalse();
    expect(next.needsBootstrap).toBeFalse();
  });

  it("PasswordLoginLifecycle_RequestedThenSucceeded_StopsLoadingAndClearsError", () => {
    const requested = loginReducer(
      initial,
      loginWithPasswordRequested({
        email: "a",
        password: "b",
        needsBootstrap: false,
        returnUrl: "/dashboard",
      }),
    );
    const succeeded = loginReducer(
      requested,
      loginWithPasswordSucceeded({ returnUrl: "/dashboard" }),
    );
    expect(requested.loading).toBeTrue();
    expect(succeeded.loading).toBeFalse();
    expect(succeeded.error).toBe("");
    expect(succeeded.registerMode).toBeFalse();
  });

  it("PasswordLoginFailed_ErrorReturned_StoresMessageAndStopsLoading", () => {
    const requested = loginReducer(
      initial,
      loginWithPasswordRequested({
        email: "a",
        password: "b",
        needsBootstrap: false,
        returnUrl: "/dashboard",
      }),
    );
    const failed = loginReducer(requested, loginWithPasswordFailed({ message: "Invalid" }));
    expect(failed.loading).toBeFalse();
    expect(failed.error).toBe("Invalid");
  });

  it("RegisterLifecycle_RequestedThenSucceeded_ShowsSuccessNotice", () => {
    const requested = loginReducer(
      { ...initial, registerMode: true },
      registerWithPasswordRequested({ email: "a", password: "p" }),
    );
    const succeeded = loginReducer(requested, registerWithPasswordSucceeded());
    expect(requested.loading).toBeTrue();
    expect(succeeded.loading).toBeFalse();
    expect(succeeded.notice).toContain("Account created");
    expect(succeeded.registerMode).toBeFalse();
  });

  it("RegisterFailed_MessagePresent_StoresError", () => {
    const next = loginReducer(initial, registerWithPasswordFailed({ message: "Exists" }));
    expect(next.error).toBe("Exists");
  });

  it("RegisterModeToggled_EnableThenDisable_ReflectsState", () => {
    const enabled = loginReducer(initial, registerModeEnabled());
    const disabled = loginReducer(enabled, registerModeDisabled());
    expect(enabled.registerMode).toBeTrue();
    expect(disabled.registerMode).toBeFalse();
  });
});

describe("loginSelectors", () => {
  it("FeatureSelectors_StateProvided_ReturnExpectedSlices", () => {
    const featureState: LoginState = {
      oidcEnabled: true,
      needsBootstrap: true,
      loading: true,
      error: "oops",
      notice: "hello",
      registerMode: true,
    };
    const root = { login: featureState };

    expect(loginFeature.selectOidcEnabled(root)).toBeTrue();
    expect(loginFeature.selectNeedsBootstrap(root)).toBeTrue();
    expect(loginFeature.selectLoading(root)).toBeTrue();
    expect(loginFeature.selectError(root)).toBe("oops");
    expect(loginFeature.selectNotice(root)).toBe("hello");
    expect(loginFeature.selectRegisterMode(root)).toBeTrue();
  });
});
