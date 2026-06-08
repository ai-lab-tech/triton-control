/* eslint-disable @typescript-eslint/no-explicit-any */
import { HttpErrorResponse, HttpRequest } from "@angular/common/http";
import { TestBed } from "@angular/core/testing";
import { Router } from "@angular/router";
import { of, throwError } from "rxjs";
import { BASE_PATH } from "../../api/generated/index";
import { authInterceptor } from "./auth.interceptor";
import { AuthService } from "./auth.service";

describe("authInterceptor", () => {
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let routerMock: jasmine.SpyObj<Router>;

  beforeEach(() => {
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", [
      "getAccessToken",
      "clearLocalSession",
    ]);
    authServiceMock.getAccessToken.and.returnValue("token-1");
    routerMock = jasmine.createSpyObj<Router>("Router", ["navigate"], { url: "/instances" });
    routerMock.navigate.and.resolveTo(true);

    TestBed.configureTestingModule({
      providers: [
        { provide: AuthService, useValue: authServiceMock },
        { provide: Router, useValue: routerMock },
        { provide: BASE_PATH, useValue: "http://localhost:8000" },
      ],
    });
  });

  it("AuthInterceptor_RequestTargetsApi_AddsBearerTokenAndCredentials", (done) => {
    // Arrange
    const req = new HttpRequest("GET", "http://localhost:8000/api/x");
    const next = jasmine.createSpy().and.callFake((cloned: HttpRequest<unknown>) => {
      // Assert
      expect(cloned.withCredentials).toBeTrue();
      expect(cloned.headers.get("Authorization")).toBe("Bearer token-1");
      return of({ ok: true } as any);
    });

    // Act
    TestBed.runInInjectionContext(() => authInterceptor(req, next)).subscribe({
      complete: () => done(),
    });
  });

  it("AuthInterceptor_401OnNonLoginRequest_ClearsSessionAndNavigatesLogin", (done) => {
    // Arrange
    const req = new HttpRequest("GET", "http://localhost:8000/api/protected");
    const next = () =>
      throwError(() => new HttpErrorResponse({ status: 401, error: { detail: "unauthorized" } }));

    // Act
    TestBed.runInInjectionContext(() => authInterceptor(req, next)).subscribe({
      error: () => {
        // Assert
        expect(authServiceMock.clearLocalSession).toHaveBeenCalled();
        expect(routerMock.navigate).toHaveBeenCalled();
        done();
      },
    });
  });
});
