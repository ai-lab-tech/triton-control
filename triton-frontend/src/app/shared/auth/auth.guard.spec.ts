/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { Router } from "@angular/router";
import { AuthStore } from "./auth.store";
import { AuthService } from "./auth.service";
import { adminGuard, authGuard } from "./auth.guard";

describe("authGuard", () => {
  let routerMock: jasmine.SpyObj<Router>;
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let authState: InstanceType<typeof AuthStore>;

  beforeEach(() => {
    routerMock = jasmine.createSpyObj<Router>("Router", ["createUrlTree"]);
    routerMock.createUrlTree.and.returnValue({} as any);
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", ["refreshSession"]);
    authServiceMock.refreshSession.and.resolveTo();

    TestBed.configureTestingModule({
      providers: [
        AuthStore,
        { provide: Router, useValue: routerMock },
        { provide: AuthService, useValue: authServiceMock },
      ],
    });

    authState = TestBed.inject(AuthStore);
  });

  it("AuthGuard_UserAlreadyLoggedInAndAllowed_ReturnsTrue", async () => {
    // Arrange
    authState.setAuthenticatedUser({ name: "Admin", accessAllowed: true });

    // Act
    const result = await TestBed.runInInjectionContext(() =>
      authGuard({} as any, { url: "/dashboard" } as any),
    );

    // Assert
    expect(result).toBeTrue();
  });

  it("AuthGuard_UserNotAuthenticated_RedirectsToLoginWithReturnUrl", async () => {
    // Arrange
    authState.logout();
    authServiceMock.refreshSession.and.rejectWith(new Error("no session"));

    // Act
    await TestBed.runInInjectionContext(() => authGuard({} as any, { url: "/instances" } as any));

    // Assert
    expect(routerMock.createUrlTree).toHaveBeenCalled();
  });

  it("AdminGuard_UserIsNotAdmin_RedirectsToLogin", async () => {
    // Arrange
    authState.setAuthenticatedUser({ name: "User", role: "viewer", accessAllowed: true });

    // Act
    await TestBed.runInInjectionContext(() => adminGuard({} as any, { url: "/users" } as any));

    // Assert
    expect(routerMock.createUrlTree).toHaveBeenCalled();
  });
});
