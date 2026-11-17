import { TestBed } from "@angular/core/testing";
import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { ActivatedRouteSnapshot, Router, RouterStateSnapshot, provideRouter } from "@angular/router";
import { InstallationService } from "./installation.service";
import { installationGuard } from "./installation.guard";

describe("Installation policy", () => {
  beforeEach(() => TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
  }));
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  for (const mode of ["development", "production"] as const) {
    it(`uses the server's ${mode} capabilities for deep links`, async () => {
      // Arrange
      const route = { data: { capability: "development_tools" } } as unknown as ActivatedRouteSnapshot;
      // Act
      const result = TestBed.runInInjectionContext(() => installationGuard(route, {} as RouterStateSnapshot));
      const request = TestBed.inject(HttpTestingController).expectOne("/api/capabilities");
      expect(request.request.withCredentials).toBeTrue();
      request.flush({ mode, capabilities: { development_tools: mode === "development" }, explanation: mode });
      // Assert
      const decision = await result;
      if (mode === "development") expect(decision).toBeTrue();
      else expect(TestBed.inject(Router).serializeUrl(decision as never)).toContain("installation-restricted");
    });
  }

  it("clears previously allowed capabilities when the backend is unavailable", async () => {
    // Arrange
    const installation = TestBed.inject(InstallationService);
    installation.snapshot.set({ mode: "development", capabilities: { development_tools: true }, explanation: "Development" });
    // Act
    const result = installation.load();
    TestBed.inject(HttpTestingController).expectOne("/api/capabilities").flush({}, { status: 503, statusText: "Unavailable" });
    // Assert
    expect(await result).toBeFalse();
    expect(installation.allows("development_tools")).toBeFalse();
    expect(installation.snapshot()).toBeNull();
  });

  it("denies unknown capabilities before a policy has loaded", () => {
    // Arrange
    const installation = TestBed.inject(InstallationService);
    // Act / Assert
    expect(installation.allows("direct_deployment")).toBeFalse();
  });
});
