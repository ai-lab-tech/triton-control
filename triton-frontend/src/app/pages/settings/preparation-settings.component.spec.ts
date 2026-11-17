import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { PreparationSettingsComponent } from "./preparation-settings.component";

const url = "/api/settings/model-preparation";
const limits = {
  max_object_gib: 10,
  max_repository_gib: 15,
  max_workspace_gib: 15,
  capture_timeout_seconds: 3600,
  download_timeout_seconds: 3600,
};

describe("PreparationSettingsComponent", () => {
  let http: HttpTestingController;
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PreparationSettingsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it("loads deployment defaults and saves administrator overrides", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http.expectOne(url).flush({ limits, defaults: limits, source: "deployment" });
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.draft = {
      ...limits,
      max_object_gib: 24,
      max_repository_gib: 32,
      max_workspace_gib: 40,
    };
    // Act
    const pending = component.save();
    const put = http.expectOne({ method: "PUT", url });
    const body = put.request.body;
    put.flush({ limits: body, defaults: limits, source: "admin" });
    await pending;
    // Assert
    expect(body.max_object_gib).toBe(24);
    expect(component.source()).toBe("admin");
    expect(component.message()).toContain("saved");
  });

  it("rejects inconsistent or fractional sizes before sending requests", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http.expectOne(url).flush({ limits, defaults: limits, source: "deployment" });
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.draft = { ...limits, max_object_gib: 20.5 };
    // Act
    await component.save();
    // Assert
    expect(component.valid()).toBeFalse();
    http.expectNone(url);
  });

  it("reset removes the database override and displays deployment values", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http
      .expectOne(url)
      .flush({
        limits: { ...limits, capture_timeout_seconds: 7200 },
        defaults: limits,
        source: "admin",
      });
    await fixture.whenStable();
    // Act
    const pending = fixture.componentInstance.reset();
    http
      .expectOne({ method: "DELETE", url })
      .flush({ limits, defaults: limits, source: "deployment" });
    await pending;
    // Assert
    expect(fixture.componentInstance.source()).toBe("deployment");
    expect(fixture.componentInstance.draft?.capture_timeout_seconds).toBe(3600);
  });
});
