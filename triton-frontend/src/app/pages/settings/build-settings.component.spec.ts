import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { BASE_PATH } from "../../api/generated";
import { BuildSettingsComponent } from "./build-settings.component";

describe("BuildSettingsComponent", () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [BuildSettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "" },
      ],
    });
  });
  it("saves a blank local destination and clears the submitted credential editor", async () => {
    const fixture = TestBed.createComponent(BuildSettingsComponent);
    const http = TestBed.inject(HttpTestingController);
    const url = "/api/settings/serving-image-builds/destination";
    const state = {
      destination: null,
      has_override: false,
      status: "The cluster builder still needs setup.",
      builder: { executor_image: "registry.example/kaniko@sha256:fixture", release: "v1.29.1" },
    };
    http.expectOne(url).flush(state);
    await fixture.whenStable();
    fixture.detectChanges();
    const component = fixture.componentInstance;
    await fixture.whenStable();
    fixture.detectChanges();
    const saveButton: HTMLButtonElement =
      fixture.nativeElement.querySelector('button[type="submit"]');
    expect(saveButton.disabled).toBeFalse();
    const advanced = Array.from(
      fixture.nativeElement.querySelectorAll("details") as NodeListOf<HTMLDetailsElement>,
    ).find((element) => element.querySelector("summary")?.textContent?.trim() === "Advanced");
    expect(advanced?.open).toBeFalse();
    expect(advanced?.textContent).toContain("registry.example/kaniko v1.29.1");
    expect(advanced?.textContent).toContain("sha256:fixture");
    expect(fixture.nativeElement.querySelector("details").open).toBeFalse();

    component.pushCredentials = '{"auths":{"registry":{"auth":"fixture"}}}';
    const saved = component.save();
    const request = http.expectOne(url);
    const expected = {
      reuse_pull_credentials: false,
      dockerconfigjson: component.pushCredentials,
      namespace: "triton-builds",
      allowed_repositories: [],
    };
    request.flush({
      ...state,
      destination: { namespace: expected.namespace, allowed_repositories: [] },
      push_credentials_saved: true,
      has_override: true,
    });
    await saved;

    expect(component.pushCredentials).toBe("");
    expect(request.request.body).toEqual(expected);
    expect(component.settings()?.status).toBe(state.status);
    http.verify();
  });
  it("submits reuse without sending credentials typed before checking the box", async () => {
    const fixture = TestBed.createComponent(BuildSettingsComponent);
    const http = TestBed.inject(HttpTestingController);
    const url = "/api/settings/serving-image-builds/destination";
    const state = { destination: null, status: "", has_override: false };
    http.expectOne(url).flush(state);
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.pushCredentials = "unused";
    component.reusePull = true;

    const saved = component.save();
    const req = http.expectOne(url);
    req.flush({ ...state, reuse_pull_credentials: true });
    await saved;

    expect(req.request.body.reuse_pull_credentials).toBeTrue();
    expect(req.request.body.dockerconfigjson).toBeUndefined();
    expect(component.reusePull).toBeTrue();
    http.verify();
  });
});
