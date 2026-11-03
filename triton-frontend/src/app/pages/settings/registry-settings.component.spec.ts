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
  it("loads status without fetching credentials, saves and clears the draft", async () => {
    const component = TestBed.createComponent(RegistrySettingsComponent).componentInstance;
    const http = TestBed.inject(HttpTestingController);
    http.expectOne("/api/settings/registry").flush({ configured: true });
    await Promise.resolve();
    expect(component.configured()).toBeTrue();
    expect(component.draft).toBe("");
    component.draft = '{"auths":{"registry":{"auth":"secret"}}}';
    const saved = component.save(false);
    const req = http.expectOne("/api/settings/registry");
    expect(req.request.method).toBe("PUT");
    expect(req.request.body.dockerconfigjson).toBe(component.draft);
    req.flush({ configured: true });
    await saved;
    expect(component.draft).toBe("");
    const removed = component.save(true);
    const remove = http.expectOne("/api/settings/registry");
    expect(remove.request.body).toEqual({ dockerconfigjson: "" });
    remove.flush({ configured: false });
    await removed;
    expect(component.configured()).toBeFalse();
  });
});
