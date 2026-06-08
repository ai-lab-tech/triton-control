import { ActivatedRoute, Router } from "@angular/router";
import { ApplicationRef } from "@angular/core";
import { TestBed } from "@angular/core/testing";
import { provideStore, provideState, Store } from "@ngrx/store";
import { provideEffects } from "@ngrx/effects";
import { AuthStore } from "../../shared/auth/auth.store";
import { AuthService } from "../../shared/auth/auth.service";
import { LoginPageComponent } from "./login-page.component";
import { LoginEffects } from "../../state/login/login.effects";
import { LOGIN_FEATURE_KEY, loginReducer } from "../../state/login/login.reducer";
import {
  bootstrapStatusLoaded,
  loginWithPasswordFailed,
  registerModeEnabled,
  registerWithPasswordSucceeded,
} from "../../state/login/login.actions";

describe("LoginPageComponent", () => {
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let routerMock: jasmine.SpyObj<Router>;
  let returnUrl = "/dashboard";
  let authState: InstanceType<typeof AuthStore>;
  let store: Store;

  beforeEach(async () => {
    returnUrl = "/dashboard";
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", [
      "getBootstrapStatus",
      "refreshSession",
      "loginWithPassword",
      "registerWithPassword",
      "registerFirstUser",
      "loginWithOidc",
    ]);
    authServiceMock.getBootstrapStatus.and.resolveTo({ oidcEnabled: false, needsSetup: false });
    authServiceMock.refreshSession.and.resolveTo();
    authServiceMock.loginWithPassword.and.resolveTo();
    authServiceMock.registerWithPassword.and.resolveTo();
    authServiceMock.registerFirstUser.and.resolveTo();
    routerMock = jasmine.createSpyObj<Router>("Router", ["navigateByUrl"]);
    Object.defineProperty(routerMock, "url", { value: "/signin", configurable: true });
    routerMock.navigateByUrl.and.resolveTo(true);

    await TestBed.configureTestingModule({
      imports: [LoginPageComponent],
      providers: [
        AuthStore,
        provideStore(),
        provideState(LOGIN_FEATURE_KEY, loginReducer),
        provideEffects([LoginEffects]),
        { provide: AuthService, useValue: authServiceMock },
        { provide: Router, useValue: routerMock },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: { get: () => returnUrl } } },
        },
      ],
    }).compileComponents();

    authState = TestBed.inject(AuthStore);
    store = TestBed.inject(Store);
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it("LoginWithPassword_OidcEnabled_ShowsDisabledErrorMessage", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: true, needsBootstrap: false }));
    component.loginWithPassword();
    expect(component.error()).toContain("disabled");
  });

  it("LoginWithPassword_ValidCredentials_NavigatesToReturnUrl", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.email = "user@example.com";
    component.password = "Validpass123!";
    authState.setAuthenticatedUser({ name: "User", accessAllowed: true });
    component.loginWithPassword();
    await fixture.whenStable();
    await fixture.whenStable();
    expect(authServiceMock.loginWithPassword).toHaveBeenCalled();
    expect(routerMock.navigateByUrl).toHaveBeenCalledWith("/dashboard");
  });

  it("SessionRefresh_ExistingAllowedUser_AutoNavigatesToReturnUrl", async () => {
    Object.defineProperty(routerMock, "url", {
      value: "/signin?returnUrl=%2Finstances",
      configurable: true,
    });
    returnUrl = "/instances";
    authServiceMock.refreshSession.and.callFake(async () => {
      authState.setAuthenticatedUser({ name: "User", accessAllowed: true });
    });

    TestBed.createComponent(LoginPageComponent);
    await TestBed.inject(ApplicationRef).whenStable();

    expect(authServiceMock.refreshSession).toHaveBeenCalled();
    expect(authState.isLoggedIn()).toBeTrue();
    expect(routerMock.navigateByUrl).toHaveBeenCalledWith("/instances", { replaceUrl: true });
  });

  it("RegisterWithPassword_MismatchedPasswords_ShowsValidationError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.email = "user@example.com";
    component.password = "Validpass123!";
    component.confirmPassword = "Validpass456!";
    component.registerWithPassword();
    expect(component.error()).toContain("match");
  });

  it("LoginWithPassword_EmailMissing_ShowsRequiredValidationError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.email = "   ";
    component.password = "Validpass123!";
    component.loginWithPassword();
    expect(component.error()).toContain("required");
  });

  it("LoginWithPassword_InvalidEmail_ShowsEmailValidationError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.email = "invalid-email";
    component.password = "Validpass123!";
    component.loginWithPassword();
    expect(component.error()).toContain("valid email");
  });

  it("LoginWithPassword_BootstrapAndMissingConfirmation_ShowsConfirmationError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: false, needsBootstrap: true }));
    component.email = "user@example.com";
    component.password = "Validpass123!";
    component.confirmPassword = "";
    component.loginWithPassword();
    expect(component.error()).toContain("confirm");
  });

  it("LoginWithPassword_BootstrapAndMismatchedPasswords_ShowsMismatchError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: false, needsBootstrap: true }));
    component.email = "user@example.com";
    component.password = "Validpass123!";
    component.confirmPassword = "Validpass456!";
    component.loginWithPassword();
    expect(component.error()).toContain("do not match");
  });

  it("LoginWithPassword_BootstrapAndShortPassword_ShowsLengthError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: false, needsBootstrap: true }));
    component.email = "user@example.com";
    component.password = "short";
    component.confirmPassword = "short";
    component.loginWithPassword();
    expect(component.error()).toContain("12-128");
  });

  it("LoginWithPassword_BootstrapRegisterFails_ContinuesWithLogin", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: false, needsBootstrap: true }));
    component.email = "user@example.com";
    component.password = "Validpass123!";
    component.confirmPassword = "Validpass123!";
    authServiceMock.registerFirstUser.and.rejectWith(new Error("already bootstrapped"));
    authState.setAuthenticatedUser({ name: "User", accessAllowed: true });
    component.loginWithPassword();
    await fixture.whenStable();
    expect(authServiceMock.registerFirstUser).toHaveBeenCalled();
    expect(authServiceMock.loginWithPassword).toHaveBeenCalledWith(
      "user@example.com",
      "Validpass123!",
    );
  });

  it("LoginWithPassword_LoginFails_SetsThrownErrorMessage", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.email = "user@example.com";
    component.password = "Validpass123!";
    authServiceMock.loginWithPassword.and.rejectWith(new Error("invalid credentials"));
    component.loginWithPassword();
    await fixture.whenStable();
    await fixture.whenStable();
    expect(component.error()).toBe("invalid credentials");
  });

  it("RegisterWithPassword_Success_ClearsPasswordsAndSetsNotice", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(registerModeEnabled());
    component.email = "user@example.com";
    component.password = "Validpass123!";
    component.confirmPassword = "Validpass123!";
    component.registerWithPassword();
    await fixture.whenStable();
    expect(component.registerMode()).toBeFalse();
    expect(component.password).toBe("");
    expect(component.confirmPassword).toBe("");
    expect(component.notice()).toContain("Account created");
  });

  it("RegisterWithPassword_OidcEnabled_ShowsDisabledError", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    store.dispatch(bootstrapStatusLoaded({ oidcEnabled: true, needsBootstrap: false }));
    component.registerWithPassword();
    expect(component.error()).toContain("disabled");
  });

  it("EnableRegisterMode_CurrentMessagesSet_EnablesModeAndClearsMessages", () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    store.dispatch(loginWithPasswordFailed({ message: "some error" }));
    store.dispatch(registerWithPasswordSucceeded());
    component.enableRegisterMode();
    expect(component.registerMode()).toBeTrue();
    expect(component.error()).toBe("");
    expect(component.notice()).toBe("");
  });

  it("LoginWithOidc_MethodInvoked_DelegatesToAuthService", () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    component.loginWithOidc();
    expect(authServiceMock.loginWithOidc).toHaveBeenCalled();
  });

  it("Continue_LoggedInButAccessDenied_SetsPendingApprovalError", () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "User", accessAllowed: false });
    component.continue();
    expect(component.error()).toContain("pending admin approval");
    expect(routerMock.navigateByUrl).not.toHaveBeenCalled();
  });

  it("Continue_LoggedInAndAllowed_NavigatesToDecodedReturnUrl", async () => {
    const fixture = TestBed.createComponent(LoginPageComponent);
    const component = fixture.componentInstance;
    returnUrl = "%2Fdashboard";
    authState.setAuthenticatedUser({ name: "User", accessAllowed: true });

    component.continue();
    await fixture.whenStable();

    expect(routerMock.navigateByUrl).toHaveBeenCalledWith("/dashboard", { replaceUrl: true });
  });
});
