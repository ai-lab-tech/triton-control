import { TestBed } from "@angular/core/testing";
import { AuthStore } from "./auth/auth.store";

describe("AuthStore", () => {
  beforeEach(() => {
    TestBed.configureTestingModule({});
  });

  it("SetAuthenticatedUser_ValidUserPayload_UpdatesAuthStateSignals", () => {
    // Arrange
    const service = TestBed.inject(AuthStore);

    // Act
    service.setAuthenticatedUser({
      name: "Admin User",
      email: "admin@example.com",
      role: "admin",
      authProvider: "oidc",
      accessAllowed: false,
      accessToken: "token-1",
    });

    // Assert
    expect(service.userName()).toBe("Admin User");
    expect(service.userEmail()).toBe("admin@example.com");
    expect(service.role()).toBe("admin");
    expect(service.authProvider()).toBe("oidc");
    expect(service.isAdmin()).toBeTrue();
    expect(service.accessAllowed()).toBeFalse();
    expect(service.accessToken()).toBe("token-1");
    expect(service.isLoggedIn()).toBeTrue();
  });

  it("Logout_UserWasAuthenticated_ResetsStateToDefaults", () => {
    // Arrange
    const service = TestBed.inject(AuthStore);

    // Act
    service.logout();

    // Assert
    expect(service.userName()).toBe("");
    expect(service.userEmail()).toBe("");
    expect(service.role()).toBe("");
    expect(service.authProvider()).toBe("");
    expect(service.isAdmin()).toBeFalse();
    expect(service.accessAllowed()).toBeFalse();
    expect(service.accessToken()).toBe("");
    expect(service.isLoggedIn()).toBeFalse();
  });

  it("SetAuthenticatedUser_ViewerRoleWithoutProvider_DefaultsProviderAndKeepsNonAdmin", () => {
    // Arrange
    const service = TestBed.inject(AuthStore);

    // Act
    service.setAuthenticatedUser({
      name: "Viewer",
      role: "viewer",
    });

    // Assert
    expect(service.role()).toBe("viewer");
    expect(service.authProvider()).toBe("local");
    expect(service.isAdmin()).toBeFalse();
    expect(service.accessAllowed()).toBeTrue();
  });
});
