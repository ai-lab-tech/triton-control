import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { BASE_PATH } from "../../api/generated";
import { BuildSettingsComponent } from "./build-settings.component";

describe("BuildSettingsComponent", () => {
  it("round trips policy fields and keeps the draft when qualification fails", async () => {
    TestBed.configureTestingModule({
      imports: [BuildSettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "" },
      ],
    });
    const fixture = TestBed.createComponent(BuildSettingsComponent);
    const component = fixture.componentInstance;
    const http = TestBed.inject(HttpTestingController);
    const url = "/api/settings/serving-image-builds";
    const policy = {
      namespace: "builds",
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
