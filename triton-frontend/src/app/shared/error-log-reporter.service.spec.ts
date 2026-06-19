import { HttpErrorResponse, provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";

import { BASE_PATH } from "../api/generated/index";
import { AuthService } from "./auth/auth.service";
import { AuthStore } from "./auth/auth.store";
import { ErrorLogReporterService } from "./error-log-reporter.service";

describe("ErrorLogReporterService", () => {
  let service: ErrorLogReporterService;
  let http: HttpTestingController;
  let authMock: jasmine.SpyObj<AuthService>;
  let isLoggedIn: jasmine.Spy;

  beforeEach(() => {
    authMock = jasmine.createSpyObj<AuthService>("AuthService", ["getAccessToken"]);
    authMock.getAccessToken.and.returnValue("access-token");
    isLoggedIn = jasmine.createSpy("isLoggedIn").and.returnValue(true);

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "http://localhost:8000/" },
        { provide: AuthService, useValue: authMock },
        { provide: AuthStore, useValue: { isLoggedIn } },
      ],
    });

    service = TestBed.inject(ErrorLogReporterService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it("List_WithSource_SendsFilterLimitAndAuthentication", () => {
    service.list("frontend", 25).subscribe();

    const request = http.expectOne(
      "http://localhost:8000/api/admin/error-logs?limit=25&source=frontend",
    );
    expect(request.request.headers.get("Authorization")).toBe("Bearer access-token");
    expect(request.request.withCredentials).toBeTrue();
    request.flush([]);
  });

  it("ReportError_Authenticated_RedactsSensitiveStackAndPostsContext", () => {
    const error = new Error("runtime failure");
    error.stack = "runtime failure\npassword=do-not-store\nat component";

    service.reportError(error);

    const request = http.expectOne("http://localhost:8000/api/admin/error-logs/frontend");
    expect(request.request.body).toEqual(
      jasmine.objectContaining({
        level: "ERROR",
        message: "runtime failure",
        detail: "runtime failure\n[redacted]\nat component",
        path: window.location.pathname,
        user_agent: navigator.userAgent,
      }),
    );
    request.flush({});
  });

  it("ReportHttpError_ObjectResponse_PostsSerializedStatusDetails", () => {
    service.reportHttpError(
      new HttpErrorResponse({
        status: 503,
        statusText: "Unavailable",
        error: { detail: "upstream failed" },
      }),
      "POST",
      "http://localhost:8000/api/deployments",
    );

    const request = http.expectOne("http://localhost:8000/api/admin/error-logs/frontend");
    expect(request.request.body).toEqual(
      jasmine.objectContaining({
        message: "HTTP 503 Unavailable",
        detail: '{"detail":"upstream failed"}',
        method: "POST",
        path: "http://localhost:8000/api/deployments",
        status_code: 503,
      }),
    );
    request.flush({});
  });

  it("ReportError_Unauthenticated_DoesNotSendRequest", () => {
    isLoggedIn.and.returnValue(false);

    service.reportError("ignored failure");

    expect(isLoggedIn).toHaveBeenCalled();
    http.expectNone("http://localhost:8000/api/admin/error-logs/frontend");
  });

  it("ReportError_SubmissionFails_DoesNotThrowOrRetry", () => {
    expect(() => service.reportError({ rejection: new Error("rejected") })).not.toThrow();

    const request = http.expectOne("http://localhost:8000/api/admin/error-logs/frontend");
    request.flush({ detail: "failed" }, { status: 500, statusText: "Server Error" });
    http.expectNone("http://localhost:8000/api/admin/error-logs/frontend");
  });
});
