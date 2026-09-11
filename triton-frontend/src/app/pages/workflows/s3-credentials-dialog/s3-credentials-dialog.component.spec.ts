import { TestBed } from "@angular/core/testing";
import { HttpClientTestingModule, HttpTestingController } from "@angular/common/http/testing";
import { MatDialogRef } from "@angular/material/dialog";
import { BASE_PATH } from "../../../api/generated/index";
import { S3CredentialsDialogComponent } from "./s3-credentials-dialog.component";

describe("S3CredentialsDialogComponent", () => {
  let http: HttpTestingController;
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [S3CredentialsDialogComponent, HttpClientTestingModule],
      providers: [
        { provide: MatDialogRef, useValue: { close: jasmine.createSpy("close") } },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  async function setup(profiles: object[] = [], credentials: object[] = []) {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    http.expectOne("/api/workflows/s3-credentials").flush(credentials);
    http.expectOne("/api/workflows/s3-profile-choices").flush(profiles);
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();
    return fixture;
  }
  const profile = {
    id: 9,
    name: "dev",
    endpoint: "https://minio:9000",
    bucket: "models",
    region: "us-east-1",
  };

  it("shows an empty state without manual credential fields", async () => {
    const fixture = await setup();
    expect(fixture.nativeElement.textContent).toContain("Create a profile on the S3 Profiles page");
    expect(fixture.nativeElement.querySelector("input, textarea")).toBeNull();
    expect(fixture.componentInstance.canCreate()).toBeFalse();
  });

  it("links the selected profile without requiring typed values", async () => {
    const fixture = await setup([profile]);
    const component = fixture.componentInstance;
    component.selectedProfileId = 9;
    expect(component.canCreate()).toBeTrue();
    const saving = component.createCredential();
    const req = http.expectOne("/api/workflows/s3-credentials");
    expect(req.request.body).toEqual({ name: "dev (9)", s3_profile_id: 9 });
    req.flush({ id: 1 });
    await Promise.resolve();
    await Promise.resolve();
    http.expectOne("/api/workflows/s3-credentials").flush([]);
    await saving;
    expect(component.selectedProfileId).toBeNull();
    expect(component.message()).toContain("linked");
  });

  it("shows the backend save error and keeps the profile selected for retry", async () => {
    const fixture = await setup([profile]);
    const component = fixture.componentInstance;
    component.selectedProfileId = 9;
    const saving = component.createCredential();
    http
      .expectOne("/api/workflows/s3-credentials")
      .flush(
        { detail: "The CA certificate is not valid PEM. Check the five dashes." },
        { status: 400, statusText: "Bad Request" },
      );
    await saving;
    fixture.detectChanges();
    const alert = fixture.nativeElement.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Check the five dashes");
    expect(alert.classList.contains("feedback-error")).toBeTrue();
    expect(component.selectedProfileId).toBe(9);
    expect(component.canCreate()).toBeTrue();
    expect(component.credentials()).toEqual([]);
  });

  it("prevents linking the same profile again and shows sync errors", async () => {
    const fixture = await setup(
      [profile],
      [
        {
          id: 1,
          name: "dev (9)",
          s3_profile_id: 9,
          s3_profile_name: "dev",
          secret_name: "workflow-s3-dev",
          namespace: "triton-control",
          sync_error: "Secret sync failed",
        },
      ],
    );
    fixture.componentInstance.selectedProfileId = 9;
    expect(fixture.componentInstance.canCreate()).toBeFalse();
    expect(fixture.nativeElement.textContent).toContain("Needs attention");
    expect(fixture.nativeElement.textContent).toContain("Secret sync failed");
    expect(fixture.nativeElement.textContent).toContain("Sync now");
  });
});
