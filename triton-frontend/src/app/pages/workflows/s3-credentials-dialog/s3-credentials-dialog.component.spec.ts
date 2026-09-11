import { TestBed } from "@angular/core/testing";
import { HttpClientTestingModule, HttpTestingController } from "@angular/common/http/testing";
import { MatDialogRef } from "@angular/material/dialog";

import { BASE_PATH } from "../../../api/generated/index";
import { S3CredentialsDialogComponent } from "./s3-credentials-dialog.component";

describe("S3CredentialsDialogComponent", () => {
  let http: HttpTestingController;
  let dialogRef: jasmine.SpyObj<MatDialogRef<S3CredentialsDialogComponent>>;

  beforeEach(async () => {
    dialogRef = jasmine.createSpyObj<MatDialogRef<S3CredentialsDialogComponent>>("MatDialogRef", [
      "close",
    ]);

    await TestBed.configureTestingModule({
      imports: [S3CredentialsDialogComponent, HttpClientTestingModule],
      providers: [
        { provide: MatDialogRef, useValue: dialogRef },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();

    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.match("/api/workflows/s3-profile-choices").forEach((req) => req.flush([]));
    http.verify();
  });

  async function flushMicrotasks(times = 3): Promise<void> {
    for (let i = 0; i < times; i += 1) {
      await Promise.resolve();
    }
  }

  it("loads credentials on init", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    const req = http.expectOne("/api/workflows/s3-credentials");
    expect(req.request.method).toBe("GET");
    req.flush([
      {
        id: 1,
        name: "finance",
        namespace: "triton-control",
        secret_name: "workflow-s3-finance-abc",
        access_key_id: "AKIA123",
        created_at: "2026-01-01T00:00:00",
        updated_at: "2026-01-01T00:00:00",
      },
    ]);
    await flushMicrotasks();

    expect(component.credentials().length).toBe(1);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Access Key ID:");
    expect(fixture.nativeElement.textContent).toContain("AKIA123");
  });

  it("creates a credential and reloads list", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    http.expectOne("/api/workflows/s3-credentials").flush([]);
    component.credentialName = "finance";
    component.accessKeyId = "AKIA123";
    component.secretAccessKey = "SECRET123";

    component.caCertificate = "  public-ca  ";
    const createPromise = component.createCredential();
    const createReq = http.expectOne("/api/workflows/s3-credentials");
    expect(createReq.request.method).toBe("POST");
    expect(createReq.request.body.ca_certificate).toBe("public-ca");
    createReq.flush({
      id: 2,
      name: "finance",
      namespace: "triton-control",
      secret_name: "workflow-s3-finance-def",
      access_key_id: "AKIA123",
      created_at: "2026-01-01T00:00:00",
      updated_at: "2026-01-01T00:00:00",
    });
    await flushMicrotasks();
    const listReq = http.expectOne("/api/workflows/s3-credentials");
    expect(listReq.request.method).toBe("GET");
    listReq.flush([]);
    await createPromise;

    expect(component.message()).toContain("created");
    expect(component.caCertificate).toBe("");
  });

  it("creates linked credentials using only a profile ID and displays sync errors", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;
    http.expectOne("/api/workflows/s3-credentials").flush([]);
    http.expectOne("/api/workflows/s3-profile-choices").flush([
      {
        id: 9,
        name: "MinIO",
        endpoint: "https://minio:9000",
        bucket: "models",
        region: "us-east-1",
      },
    ]);
    await flushMicrotasks();
    component.selectedProfileId = 9;
    component.selectProfile();
    component.secretAccessKey = "old manual secret";
    expect(component.canCreate()).toBeTrue();
    const saving = component.createCredential();
    const req = http.expectOne("/api/workflows/s3-credentials");
    expect(req.request.body).toEqual({ name: "MinIO", s3_profile_id: 9 });
    req.flush({ id: 1 });
    await flushMicrotasks();
    http.expectOne("/api/workflows/s3-credentials").flush([
      {
        id: 1,
        name: "MinIO",
        s3_profile_id: 9,
        s3_profile_name: "MinIO",
        sync_error: "Secret sync failed",
      },
    ]);
    await saving;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Linked profile: MinIO");
    expect(fixture.nativeElement.textContent).toContain("Secret sync failed");
    expect(fixture.nativeElement.textContent).toContain("Sync now");
  });

  it("loads a certificate file and rejects oversized uploads", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;
    http.expectOne("/api/workflows/s3-credentials").flush([]);
    const input = document.createElement("input");
    input.type = "file";
    const files = new DataTransfer();
    files.items.add(new File(["public-ca"], "ca.pem"));
    input.files = files.files;
    await component.loadCaCertificate({ target: input } as unknown as Event);
    expect(component.caCertificate).toBe("public-ca");

    const oversized = new DataTransfer();
    oversized.items.add(new File(["x".repeat(262145)], "large.pem"));
    input.files = oversized.files;
    await component.loadCaCertificate({ target: input } as unknown as Event);
    expect(component.message()).toContain("256 KiB");
    expect(component.caCertificate).toBe("public-ca");
  });

  it("toggles and resets the form before closing the dialog", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    http.expectOne("/api/workflows/s3-credentials").flush([]);

    component.toggleForm();
    expect(component.showForm()).toBeTrue();

    component.credentialName = "finance";
    component.accessKeyId = "AKIA123";
    component.secretAccessKey = "SECRET123";
    component.toggleForm();

    expect(component.showForm()).toBeFalse();
    expect(component.credentialName).toBe("");
    expect(component.accessKeyId).toBe("");
    expect(component.secretAccessKey).toBe("");

    component.close();
    expect(dialogRef.close).toHaveBeenCalled();
  });
});
