/* eslint-disable @typescript-eslint/no-explicit-any */
import { PLATFORM_ID } from "@angular/core";
import { HttpClient, HttpErrorResponse } from "@angular/common/http";
import { TestBed } from "@angular/core/testing";
import { of, throwError } from "rxjs";
import {
  AuthService as GeneratedAuthService,
  BASE_PATH,
  DefaultService,
  UsersService,
} from "../../api/generated/index";
import { AuthStore } from "./auth.store";
import { AuthService } from "./auth.service";

describe("AuthService", () => {
  let service: AuthService;
  let usersApiMock: jasmine.SpyObj<UsersService>;
  let generatedAuthMock: jasmine.SpyObj<GeneratedAuthService>;
  let defaultApiMock: jasmine.SpyObj<DefaultService>;
  let authState: InstanceType<typeof AuthStore>;
  const validToken = (() => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    return `h.${payload}.s`;
  })();

  beforeEach(() => {
    usersApiMock = jasmine.createSpyObj<UsersService>("UsersService", [
      "loginEndpointApiAuthLoginPost",
      "selfRegisterEndpointApiAuthSelfRegisterPost",
      "authOptionsEndpointApiAuthOptionsGet",
      "bootstrapStatusEndpointApiAuthBootstrapStatusGet",
      "bootstrapRegisterEndpointApiAuthBootstrapRegisterPost",
      "getOidcSettingsEndpointApiAuthSettingsGet",
      "putOidcSettingsEndpointApiAuthSettingsPut",
    ]);
    generatedAuthMock = jasmine.createSpyObj<GeneratedAuthService>("GeneratedAuthService", [
      "whoamiApiWhoamiGet",
      "logoutLogoutPost",
    ]);
    defaultApiMock = jasmine.createSpyObj<DefaultService>("DefaultService", ["authMeApiAuthMeGet"]);
    generatedAuthMock.logoutLogoutPost.and.returnValue(of({} as any));

    spyOn(localStorage, "setItem");
    spyOn(localStorage, "removeItem");
    spyOn(localStorage, "getItem").and.returnValue(null);

    TestBed.configureTestingModule({
      providers: [
        AuthService,
        AuthStore,
        { provide: HttpClient, useValue: {} },
        { provide: UsersService, useValue: usersApiMock },
        { provide: GeneratedAuthService, useValue: generatedAuthMock },
        { provide: DefaultService, useValue: defaultApiMock },
        { provide: BASE_PATH, useValue: "http://localhost:8000" },
        { provide: PLATFORM_ID, useValue: "browser" },
      ],
    });

    service = TestBed.inject(AuthService);
    authState = TestBed.inject(AuthStore);
  });

  it("LoginWithPassword_ApiReturnsToken_StoresSessionAndUpdatesState", async () => {
    // Arrange
    usersApiMock.loginEndpointApiAuthLoginPost.and.returnValue(
      of({
        access_token: "token-1",
        user: { name: "Alice", email: "a@example.com", role: "admin", auth_provider: "local" },
      } as any),
    );

    // Act
    await service.loginWithPassword("a@example.com", "pw");

    // Assert
    expect(authState.userName()).toBe("Alice");
    expect(authState.isAdmin()).toBeTrue();
    expect(localStorage.setItem).toHaveBeenCalled();
  });

  it("LoginWithPassword_ApiReturnsHttpDetail_ThrowsMappedError", async () => {
    // Arrange
    usersApiMock.loginEndpointApiAuthLoginPost.and.returnValue(
      throwError(
        () => new HttpErrorResponse({ status: 401, error: { detail: "Invalid credentials" } }),
      ),
    );

    // Act / Assert
    await expectAsync(service.loginWithPassword("a@example.com", "bad")).toBeRejectedWithError(
      "Invalid credentials",
    );
  });

  it("GetAuthOptions_ApiResponds_MapsOidcEnabledFlag", async () => {
    // Arrange
    usersApiMock.authOptionsEndpointApiAuthOptionsGet.and.returnValue(
      of({ oidc_enabled: true } as any),
    );

    // Act
    const result = await service.getAuthOptions();

    // Assert
    expect(result.oidcEnabled).toBeTrue();
  });

  it("SaveOidcSettings_ApiReturnsUpdatedSettings_MapsResponseToDomainModel", async () => {
    // Arrange
    usersApiMock.putOidcSettingsEndpointApiAuthSettingsPut.and.returnValue(
      of({
        oidc_enabled: true,
        issuer: "iss",
        client_id: "cid",
        client_secret: "",
        client_secret_configured: true,
        redirect_uri: "cb",
        scopes: "openid",
        strict_discovery_document_validation: false,
        ca_certificate: "cert",
        api_base_url: "http://localhost:8000",
        config_source: "db",
        kubernetes_enabled: true,
      } as any),
    );

    // Act
    const result = await service.saveOidcSettings({
      oidcEnabled: true,
      issuer: "iss",
      clientId: "cid",
      clientSecret: "sec",
      clientSecretConfigured: true,
      redirectUri: "cb",
      scopes: "openid",
      strictDiscoveryDocumentValidation: false,
      caCertificate: "cert",
      apiBaseUrl: "http://localhost:8000",
      configSource: "db",
      kubernetesEnabled: true,
    });

    // Assert
    expect(result?.oidcEnabled).toBeTrue();
    expect(result?.clientId).toBe("cid");
    expect(result?.clientSecret).toBe("");
    expect(result?.clientSecretConfigured).toBeTrue();
  });

  it("RefreshSession_DefaultApiUnauthenticated_ClearsStateAndStorage", async () => {
    // Arrange
    defaultApiMock.authMeApiAuthMeGet.and.returnValue(of({ authenticated: false } as any));

    // Act
    await service.refreshSession();

    // Assert
    expect(authState.isLoggedIn()).toBeFalse();
    expect(localStorage.removeItem).toHaveBeenCalled();
  });

  it("LoginWithPassword_TokenMissing_ThrowsMissingTokenError", async () => {
    // Arrange
    usersApiMock.loginEndpointApiAuthLoginPost.and.returnValue(
      of({ access_token: "", user: { email: "a@example.com" } } as any),
    );

    // Act / Assert
    await expectAsync(service.loginWithPassword("a@example.com", "pw")).toBeRejectedWithError(
      "Missing token in login response",
    );
  });

  it("RegisterWithPassword_HttpDetailProvided_ThrowsMappedError", async () => {
    // Arrange
    usersApiMock.selfRegisterEndpointApiAuthSelfRegisterPost.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 400, error: { detail: "already exists" } })),
    );

    // Act / Assert
    await expectAsync(
      service.registerWithPassword("a@example.com", "validpass123"),
    ).toBeRejectedWithError("already exists");
  });

  it("Logout_BrowserRuntime_ClearsSessionAndCallsBackendLogout", () => {
    // Arrange
    authState.setAuthenticatedUser({ name: "Alice", accessAllowed: true, accessToken: "t" });

    // Act
    service.logout();

    // Assert
    expect(authState.isLoggedIn()).toBeFalse();
    expect(localStorage.removeItem).toHaveBeenCalled();
    expect(generatedAuthMock.logoutLogoutPost).toHaveBeenCalled();
  });

  it("GetBootstrapStatus_ApiReturnsFlags_MapsExpectedValues", async () => {
    // Arrange
    usersApiMock.bootstrapStatusEndpointApiAuthBootstrapStatusGet.and.returnValue(
      of({ oidc_enabled: true, needs_setup: true } as any),
    );

    // Act
    const result = await service.getBootstrapStatus();

    // Assert
    expect(result).toEqual({ oidcEnabled: true, needsSetup: true });
  });

  it("RegisterFirstUser_ValidPayload_CallsBootstrapRegisterApi", async () => {
    // Arrange
    usersApiMock.bootstrapRegisterEndpointApiAuthBootstrapRegisterPost.and.returnValue(
      of({} as any),
    );

    // Act
    await service.registerFirstUser("a@example.com", "validpass123");

    // Assert
    expect(usersApiMock.bootstrapRegisterEndpointApiAuthBootstrapRegisterPost).toHaveBeenCalled();
  });

  it("GetOidcSettings_ApiReturnsDto_MapsDomainFields", async () => {
    // Arrange
    usersApiMock.getOidcSettingsEndpointApiAuthSettingsGet.and.returnValue(
      of({
        oidc_enabled: true,
        issuer: "iss",
        client_id: "cid",
        client_secret: "",
        client_secret_configured: true,
        redirect_uri: "cb",
        scopes: "openid profile",
        strict_discovery_document_validation: true,
        ca_certificate: "cert",
        api_base_url: "http://localhost:8000",
        config_source: "db",
        kubernetes_enabled: false,
      } as any),
    );

    // Act
    const result = await service.getOidcSettings();

    // Assert
    expect(result.oidcEnabled).toBeTrue();
    expect(result.clientSecret).toBe("");
    expect(result.clientSecretConfigured).toBeTrue();
    expect(result.strictDiscoveryDocumentValidation).toBeTrue();
    expect(result.caCertificate).toBe("cert");
  });

  it("SaveOidcSettings_HttpDetailProvided_ThrowsMappedError", async () => {
    // Arrange
    usersApiMock.putOidcSettingsEndpointApiAuthSettingsPut.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 400, error: { detail: "invalid issuer" } })),
    );

    // Act / Assert
    await expectAsync(
      service.saveOidcSettings({
        oidcEnabled: true,
        issuer: "iss",
        clientId: "cid",
        clientSecret: "sec",
        clientSecretConfigured: true,
        redirectUri: "cb",
        scopes: "openid",
        strictDiscoveryDocumentValidation: false,
        caCertificate: "",
        apiBaseUrl: "http://localhost:8000",
        configSource: "db",
        kubernetesEnabled: false,
      }),
    ).toBeRejectedWithError("invalid issuer");
  });

  it("RefreshSession_DefaultApiFailsAndOidcSessionAuthenticated_UsesOidcUser", async () => {
    // Arrange
    defaultApiMock.authMeApiAuthMeGet.and.returnValue(throwError(() => new Error("backend down")));
    usersApiMock.authOptionsEndpointApiAuthOptionsGet.and.returnValue(
      of({ oidc_enabled: true } as any),
    );
    generatedAuthMock.whoamiApiWhoamiGet.and.returnValue(
      of({
        authenticated: true,
        user: {
          preferred_username: "oidc-user",
          email: "oidc@example.com",
          role: "viewer",
          access_allowed: true,
        },
      } as any),
    );

    // Act
    await service.refreshSession();

    // Assert
    expect(authState.userName()).toBe("oidc-user");
    expect(authState.isLoggedIn()).toBeTrue();
  });

  it("RefreshSession_DefaultAndOidcFail_ThrowsNotAuthenticated", async () => {
    // Arrange
    defaultApiMock.authMeApiAuthMeGet.and.returnValue(throwError(() => new Error("backend down")));
    generatedAuthMock.whoamiApiWhoamiGet.and.returnValue(of({ authenticated: false } as any));

    // Act / Assert
    await expectAsync(service.refreshSession()).toBeRejectedWithError("Not authenticated");
    expect(authState.isLoggedIn()).toBeFalse();
  });

  it("GetOidcSession_ApiReturnsPayload_MapsAuthenticatedAndUser", async () => {
    // Arrange
    generatedAuthMock.whoamiApiWhoamiGet.and.returnValue(
      of({ authenticated: true, user: { name: "OIDC User", email: "oidc@example.com" } } as any),
    );

    // Act
    const result = await service.getOidcSession();

    // Assert
    expect(result.authenticated).toBeTrue();
    expect(result.user?.name).toBe("OIDC User");
  });

  it("Init_TokenStoredInLocalStorage_RehydratesStateAndRefreshesSession", async () => {
    // Arrange
    (localStorage.getItem as jasmine.Spy).and.returnValue(validToken);
    defaultApiMock.authMeApiAuthMeGet.and.returnValue(
      of({
        authenticated: true,
        user: { name: "Alice", role: "admin" },
        access_allowed: true,
      } as any),
    );

    // Act
    await service.init();

    // Assert
    expect(authState.accessToken()).toBe(validToken);
    expect(authState.isLoggedIn()).toBeTrue();
  });

  it("GetAuthOptions_ApiReturnsNoFlag_DefaultsToOidcDisabled", async () => {
    // Arrange
    usersApiMock.authOptionsEndpointApiAuthOptionsGet.and.returnValue(of({} as any));

    // Act
    const result = await service.getAuthOptions();

    // Assert
    expect(result.oidcEnabled).toBeFalse();
  });

  it("RefreshSession_DefaultApiAuthenticated_MapsLocalUserState", async () => {
    // Arrange
    authState.setAccessToken("token-x");
    defaultApiMock.authMeApiAuthMeGet.and.returnValue(
      of({
        authenticated: true,
        access_allowed: false,
        user: { name: "Bob", email: "b@example.com", role: "viewer", auth_provider: "local" },
      } as any),
    );

    // Act
    await service.refreshSession();

    // Assert
    expect(authState.userName()).toBe("Bob");
    expect(authState.accessAllowed()).toBeFalse();
    expect(authState.role()).toBe("viewer");
  });
});

describe("AuthServiceServerRuntime", () => {
  let service: AuthService;
  let usersApiMock: jasmine.SpyObj<UsersService>;
  let generatedAuthMock: jasmine.SpyObj<GeneratedAuthService>;
  let defaultApiMock: jasmine.SpyObj<DefaultService>;

  beforeEach(() => {
    usersApiMock = jasmine.createSpyObj<UsersService>("UsersService", [
      "putOidcSettingsEndpointApiAuthSettingsPut",
      "authOptionsEndpointApiAuthOptionsGet",
    ]);
    generatedAuthMock = jasmine.createSpyObj<GeneratedAuthService>("GeneratedAuthService", [
      "logoutLogoutPost",
      "whoamiApiWhoamiGet",
    ]);
    defaultApiMock = jasmine.createSpyObj<DefaultService>("DefaultService", ["authMeApiAuthMeGet"]);

    TestBed.configureTestingModule({
      providers: [
        AuthService,
        AuthStore,
        { provide: HttpClient, useValue: {} },
        { provide: UsersService, useValue: usersApiMock },
        { provide: GeneratedAuthService, useValue: generatedAuthMock },
        { provide: DefaultService, useValue: defaultApiMock },
        { provide: BASE_PATH, useValue: "http://localhost:8000" },
        { provide: PLATFORM_ID, useValue: "server" },
      ],
    });

    service = TestBed.inject(AuthService);
  });

  it("Init_ServerPlatform_DoesNotTouchBrowserStorageOrApis", async () => {
    // Arrange
    const getItemSpy = spyOn(localStorage, "getItem");

    // Act
    await service.init();

    // Assert
    expect(getItemSpy).not.toHaveBeenCalled();
    expect(defaultApiMock.authMeApiAuthMeGet).not.toHaveBeenCalled();
  });

  it("SaveOidcSettings_PreflightRequiredOnServer_ReturnsNullWithoutRedirect", async () => {
    // Arrange
    usersApiMock.putOidcSettingsEndpointApiAuthSettingsPut.and.returnValue(
      of({ preflight_required: true, authorize_url: "http://localhost/login" } as any),
    );

    // Act
    const result = await service.saveOidcSettings({
      oidcEnabled: true,
      issuer: "iss",
      clientId: "cid",
      clientSecret: "sec",
      clientSecretConfigured: true,
      redirectUri: "cb",
      scopes: "openid",
      strictDiscoveryDocumentValidation: false,
      caCertificate: "",
      apiBaseUrl: "http://localhost:8000",
      configSource: "db",
      kubernetesEnabled: false,
    });

    // Assert
    expect(result).toBeNull();
  });

  it("Logout_ServerPlatform_DoesNotCallBackendLogout", () => {
    // Arrange
    generatedAuthMock.logoutLogoutPost.and.returnValue(of({} as any));

    // Act
    service.logout();

    // Assert
    expect(generatedAuthMock.logoutLogoutPost).not.toHaveBeenCalled();
  });
});
