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
  it("starts a new policy with a pinned builder and advanced settings closed", async () => {
    const fixture = TestBed.createComponent(BuildSettingsComponent);
    const http = TestBed.inject(HttpTestingController);
    http
      .expectOne("/api/settings/serving-image-builds")
      .flush({ policy: null, source: "unconfigured" });
    http.expectOne("/api/s3-profiles").flush([]);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.componentInstance.builderImage).toMatch(
      /^ghcr\.io\/osscontainertools\/kaniko@sha256:[a-f0-9]{64}$/,
    );
    expect(fixture.componentInstance.builderRelease).toBe("v1.29.1");
    expect(fixture.nativeElement.querySelector("#build-image").closest("details").open).toBeFalse();
    expect(fixture.componentInstance.enabled).toBeFalse();
    http.verify();
  });

  it("round trips policy fields and keeps the draft when qualification fails", async () => {
    const fixture = TestBed.createComponent(BuildSettingsComponent);
    const component = fixture.componentInstance;
    const http = TestBed.inject(HttpTestingController);
    const url = "/api/settings/serving-image-builds";
    const policy = {
      namespace: "builds",
      executor_image: "registry.test/builder@sha256:" + "a".repeat(64),
      release: "v2.0.0",
      s3_profile_name: "staging",
      allowed_repositories: ["registry.test/team/images"],
      egress_cidrs: ["10.10.1.0/24"],
      memory_mib: 1024,
    };
    http.expectOne(url).flush({ policy, source: "deployment" });
    http.expectOne("/api/s3-profiles").flush([{ name: "staging", bucket: "builds", prefix: "" }]);
    await fixture.whenStable();

    component.enabled = true;
    const saved = component.save();
    const request = http.expectOne(url);
    request.flush({ detail: "Qualification required" }, { status: 403, statusText: "Forbidden" });
    await saved;

    expect(request.request.body).toEqual(jasmine.objectContaining({ ...policy, enabled: true }));
    expect(component.message()).toBe("Qualification required");
    expect(component.values["namespace"]).toBe("builds");
    expect(component.source()).toBe("deployment");
    http.verify();
  });
});
