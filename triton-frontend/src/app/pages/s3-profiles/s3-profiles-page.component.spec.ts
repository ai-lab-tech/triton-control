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

  it("SaveProfile_HttpEndpoint_ClearsCertificateAndReloadsProfiles", async () => {
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
      prefix: "will-not-save",
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
      prefix: "",
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
});
