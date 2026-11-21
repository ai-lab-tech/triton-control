import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestbedHarnessEnvironment } from "@angular/cdk/testing/testbed";
import { TestBed } from "@angular/core/testing";
import { MatCheckboxHarness } from "@angular/material/checkbox/testing";
import { PreparationSettingsComponent } from "./preparation-settings.component";

const url = "/api/settings/model-preparation";
const limits = {
  max_input_gib: 15,
  max_workspace_storage_gib: 50,
  transfer_timeout_seconds: 3600,
  require_s3_versioning: true,
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
      max_input_gib: 40,
      max_workspace_storage_gib: 100,
      transfer_timeout_seconds: 7200,
    };
    // Act
    const pending = component.save();
    const put = http.expectOne({ method: "PUT", url });
    const body = put.request.body;
    put.flush({ limits: body, defaults: limits, source: "admin" });
    await pending;
    // Assert
    expect(body).toEqual({
      max_input_gib: 40,
      max_workspace_storage_gib: 100,
      transfer_timeout_seconds: 7200,
      require_s3_versioning: true,
    });
    expect(component.source()).toBe("admin");
    expect(component.message()).toContain("saved");
  });

  it("saves the unchecked versioning requirement and displays the warning", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http.expectOne(url).flush({ limits, defaults: limits, source: "deployment" });
    await fixture.whenStable();
    fixture.detectChanges();
    const loader = TestbedHarnessEnvironment.loader(fixture);
    const checkbox = await loader.getHarness(MatCheckboxHarness);

    // Act
    await checkbox.uncheck();
    const pending = fixture.componentInstance.save();
    const put = http.expectOne({ method: "PUT", url });
    put.flush({ limits: put.request.body, defaults: limits, source: "admin" });
    await pending;
    fixture.detectChanges();

    // Assert
    expect(put.request.body.require_s3_versioning).toBeFalse();
    expect(fixture.nativeElement.textContent).toContain("Unversioned files can be overwritten");
  });

  it("rejects inconsistent or fractional sizes before sending requests", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http.expectOne(url).flush({ limits, defaults: limits, source: "deployment" });
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.draft = { ...limits, max_input_gib: 20.5 };
    // Act
    await component.save();
    // Assert
    expect(component.valid()).toBeFalse();
    http.expectNone(url);
  });

  it("reset removes the database override and displays deployment values", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationSettingsComponent);
    http.expectOne(url).flush({
      limits: { ...limits, transfer_timeout_seconds: 7200 },
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
    expect(fixture.componentInstance.draft?.transfer_timeout_seconds).toBe(3600);
  });
});
