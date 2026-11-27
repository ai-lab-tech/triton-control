import { InstallationService } from "../../shared/installation/installation.service";
import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { NoopAnimationsModule } from "@angular/platform-browser/animations";

import { BASE_PATH } from "../../api/generated/index";
import { AuthStore } from "../../shared/auth/auth.store";
import { S3ProfilesPageComponent } from "./s3-profiles-page.component";

describe("S3ProfilesPageComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NoopAnimationsModule, S3ProfilesPageComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "http://localhost:8000/" },
      ],
    }).compileComponents();
    TestBed.inject(InstallationService).snapshot.set({
      mode: "development",
      explanation: "Development",
      capabilities: {
        serving_read: true,
        artifact_read: true,
        interactive_inference: true,
        development_tools: true,
        development_jobs: true,
        direct_deployment: true,
        model_write: true,
        artifact_write: true,
        trust_admin: true,
        credential_admin: true,
        identity_admin: true,
      },
    });

    TestBed.inject(AuthStore).setAuthenticatedUser({
      name: "Admin",
      role: "admin",
      accessAllowed: true,
    });
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
    TestBed.inject(AuthStore).logout();
  });

  function createComponent(profiles: unknown[] = []) {
    const fixture = TestBed.createComponent(S3ProfilesPageComponent);
    TestBed.inject(HttpTestingController)
      .expectOne("http://localhost:8000/api/s3-profiles")
      .flush(profiles);
    return fixture;
  }

  it("MemberCanViewGlobalProfilesWithoutManagementControls", async () => {
    // Arrange
    TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Member", role: "member" });
    const fixture = createComponent([
      {
        id: 7,
        name: "global-models",
        used_by_mlflow: true,
        endpoint: "https://s3",
        bucket: "models",
      },
    ]);
    // Act
    await fixture.whenStable();
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.querySelector(".profile-name").textContent).toBe("global-models");
    expect(fixture.nativeElement.querySelector(".usage-badge").textContent).toContain(
      "MLflow artifact store",
    );
    expect(fixture.nativeElement.querySelector("input")).toBeNull();
  });

  for (const action of ["saveProfile", "deleteProfile"] as const) {
    it(`blocks member ${action} without sending a mutation`, async () => {
      // Arrange
      TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Member", role: "member" });
      const fixture = createComponent([
        { id: 7, name: "dev", endpoint: "https://s3", bucket: "models" },
      ]);
      await fixture.whenStable();
      // Act
      await fixture.componentInstance[action]();
      // Assert
      TestBed.inject(HttpTestingController).expectNone((request) => request.method !== "GET");
    });
  }

  it("shows MLflow usage separately from the editable profile name", async () => {
    const fixture = createComponent([
      { id: 7, name: "dev", used_by_mlflow: true, endpoint: "https://s3", bucket: "models" },
    ]);
    await fixture.whenStable();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector("#s3-profile-name").value).toBe("dev");
    expect(fixture.nativeElement.querySelector(".profile-usage dt").textContent).toBe("Usage");
    expect(fixture.nativeElement.querySelector(".profile-usage dd").textContent).toBe(
      "MLflow artifact store",
    );
    const trigger = fixture.nativeElement.querySelector(".profile-entry");
    expect(trigger.querySelector(".profile-name").textContent).toBe("dev");
    expect(trigger.querySelector(".usage-badge")).not.toBeNull();
  });

  it("CreateComponent_ProfilesLoaded_SelectsFirstProfile", async () => {
    const fixture = createComponent([
      {
        id: 7,
        name: "dev-minio",
        endpoint: "https://host.minikube.internal:9000",
        bucket: "triton-models",
        region: "us-east-1",
        access_key: "ak",
        secret_key: "sk",
        prefix: "",
        force_path_style: true,
        ca_certificate: "cert",
      },
    ]);
    await fixture.whenStable();

    const component = fixture.componentInstance;

    expect(component.selectedId).toBe("7");
    expect(component.draft.name).toBe("dev-minio");
    expect(component.usesHttpsS3()).toBeTrue();
  });

  it("NewProfile_SelectedProfileExists_ClearsDraft", async () => {
    const fixture = createComponent([
      {
        id: 8,
        name: "http-minio",
        endpoint: "http://minio:9000",
        bucket: "bucket",
        region: "eu",
        access_key: "ak",
        secret_key: "sk",
        prefix: "",
        force_path_style: false,
        ca_certificate: "ignored",
      },
    ]);
    await fixture.whenStable();

    const component = fixture.componentInstance;
    component.newProfile();

    expect(component.selectedId).toBe("");
    expect(component.draft.name).toBe("");
    expect(component.draft.force_path_style).toBeTrue();
    expect(component.usesHttpsS3()).toBeFalse();
  });

  it("SaveProfile_HttpEndpoint_PreservesPrefixAndClearsCertificate", async () => {
    const fixture = createComponent();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.draft = {
      id: 0,
      name: " local ",
      endpoint: " http://minio:9000 ",
      bucket: " models ",
      region: "",
      access_key: " ak ",
      secret_key: "sk",
      prefix: "team/models",
      force_path_style: true,
      ca_certificate: "stale-cert",
    };

    const savePromise = component.saveProfile();
    const http = TestBed.inject(HttpTestingController);
    const saveRequest = http.expectOne("http://localhost:8000/api/s3-profiles");
    expect(saveRequest.request.method).toBe("POST");
    expect(saveRequest.request.body).toEqual({
      name: "local",
      endpoint: "http://minio:9000",
      bucket: "models",
      region: "us-east-1",
      access_key: "ak",
      secret_key: "sk",
      prefix: "team/models",
      force_path_style: true,
      ca_certificate: "",
    });
    saveRequest.flush({ ...component.draft, id: 9, name: "local" });
    await Promise.resolve();
    http
      .expectOne("http://localhost:8000/api/s3-profiles")
      .flush([{ ...component.draft, id: 9, name: "local" }]);
    await savePromise;

    expect(component.selectedId).toBe("9");
    expect(component.message()).toBe("S3 profile saved.");
    expect(component.messageTone()).toBe("success");
  });

  it("SaveProfile_ShowsCertificateValidationErrorAndPreservesDraft", async () => {
    const fixture = createComponent();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.draft = {
      id: 0,
      name: "dev",
      endpoint: "https://minio:9000",
      bucket: "models",
      region: "us-east-1",
      access_key: "ak",
      secret_key: "sk",
      prefix: "",
      force_path_style: true,
      ca_certificate: "----BEGIN CERTIFICATE-----",
    };
    const saving = component.saveProfile();
    TestBed.inject(HttpTestingController)
      .expectOne("http://localhost:8000/api/s3-profiles")
      .flush(
        {
          detail: [
            {
              loc: ["body", "ca_certificate"],
              msg: "Invalid PEM CA certificate: use five dashes.",
            },
          ],
        },
        { status: 422, statusText: "Unprocessable Entity" },
      );
    await saving;
    expect(component.message()).toContain("use five dashes");
    expect(component.messageTone()).toBe("error");
    expect(component.draft.ca_certificate).toBe("----BEGIN CERTIFICATE-----");
  });

  it("DeleteProfile_SelectedProfile_DeletesAndReloadsProfiles", async () => {
    const fixture = createComponent([
      {
        id: 10,
        name: "delete-me",
        endpoint: "https://minio:9000",
        bucket: "bucket",
        region: "us-east-1",
        access_key: "ak",
        secret_key: "sk",
        prefix: "",
        force_path_style: true,
        ca_certificate: "cert",
      },
    ]);
    await fixture.whenStable();

    const component = fixture.componentInstance;
    const deletePromise = component.deleteProfile();
    const http = TestBed.inject(HttpTestingController);
    const deleteRequest = http.expectOne("http://localhost:8000/api/s3-profiles/10");
    expect(deleteRequest.request.method).toBe("DELETE");
    deleteRequest.flush({});
    await Promise.resolve();
    http.expectOne("http://localhost:8000/api/s3-profiles").flush([]);
    await deletePromise;

    expect(component.selectedId).toBe("");
    expect(component.message()).toBe("S3 profile deleted.");
  });
  it("production admins can rotate credentials but cannot edit scope or create profiles", async () => {
    // Arrange
    TestBed.inject(InstallationService).snapshot.set({
      mode: "production",
      explanation: "Production",
      capabilities: { credential_admin: true, trust_admin: false },
    });
    const profile = {
      id: 7,
      name: "approved",
      endpoint: "https://s3",
      bucket: "models",
      region: "us-east-1",
      prefix: "approved",
      access_key: "old",
      secret_key: "",
      ca_certificate: "",
    };
    const fixture = createComponent([profile]);
    await fixture.whenStable();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.draft.access_key = "rotated";
    component.draft.secret_key = "new-secret";
    // Act
    component.newProfile();
    const saving = component.saveProfile();
    const http = TestBed.inject(HttpTestingController);
    const request = http.expectOne("http://localhost:8000/api/s3-profiles/7");
    // Assert
    expect(component.selectedId).toBe("7");
    expect(fixture.nativeElement.querySelector("#s3-profile-bucket").disabled).toBeTrue();
    expect(fixture.nativeElement.querySelector("#s3-profile-secret-key").disabled).toBeFalse();
    expect(request.request.body).toEqual({ access_key: "rotated", secret_key: "new-secret" });
    request.flush(profile);
    await Promise.resolve();
    http.expectOne("http://localhost:8000/api/s3-profiles").flush([profile]);
    await saving;
  });
});
