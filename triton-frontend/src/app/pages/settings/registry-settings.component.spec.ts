import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { RegistrySettingsComponent } from "./registry-settings.component";
import { BASE_PATH } from "../../api/generated";

describe("RegistrySettingsComponent", () => {
  beforeEach(() =>
    TestBed.configureTestingModule({
      imports: [RegistrySettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "" },
      ],
    }),
  );
  afterEach(() => TestBed.inject(HttpTestingController).verify());
  async function configuredComponent() {
    const component = TestBed.createComponent(RegistrySettingsComponent).componentInstance;
    TestBed.inject(HttpTestingController)
      .expectOne("/api/settings/registry")
      .flush({ configured: true });
    await Promise.resolve();
    return component;
  }

  it("loads status without populating the credential editor", async () => {
    // Arrange
    const component = TestBed.createComponent(RegistrySettingsComponent).componentInstance;
    const http = TestBed.inject(HttpTestingController);
    // Act
    http.expectOne("/api/settings/registry").flush({ configured: true });
    await Promise.resolve();
    // Assert
    expect(component.configured()).toBeTrue();
    expect(component.draft).toBe("");
  });

  it("saves credentials and clears the editor after success", async () => {
    // Arrange
    const component = await configuredComponent();
    const secret = '{"auths":{"registry":{"auth":"secret"}}}';
    component.draft = secret;
    const http = TestBed.inject(HttpTestingController);
    // Act
    const saved = component.save(false);
    const req = http.expectOne("/api/settings/registry");
    req.flush({ configured: true });
    await saved;
    // Assert
    expect(req.request.method).toBe("PUT");
    expect(req.request.body).toEqual({ dockerconfigjson: secret });
    expect(component.draft).toBe("");
    expect(component.configured()).toBeTrue();
  });

  it("removes stored credentials explicitly", async () => {
    // Arrange
    const component = await configuredComponent();
    const http = TestBed.inject(HttpTestingController);
    // Act
    const saved = component.save(true);
    const req = http.expectOne("/api/settings/registry");
    req.flush({ configured: false });
    await saved;
    // Assert
    expect(req.request.body).toEqual({ dockerconfigjson: "" });
    expect(component.configured()).toBeFalse();
  });

  it("preserves the draft when saving fails", async () => {
    // Arrange
    const component = await configuredComponent();
    component.draft = "invalid";
    const http = TestBed.inject(HttpTestingController);
    // Act
    const saved = component.save(false);
    http
      .expectOne("/api/settings/registry")
      .flush({ detail: "Invalid config" }, { status: 422, statusText: "Invalid" });
    await saved;
    // Assert
    expect(component.draft).toBe("invalid");
    expect(component.busy()).toBeFalse();
    expect(component.message()).toContain("Invalid config");
  });
});
