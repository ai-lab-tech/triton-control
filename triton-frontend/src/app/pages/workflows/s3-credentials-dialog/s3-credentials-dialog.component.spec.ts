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
  });

  it("creates a credential and reloads list", async () => {
    const fixture = TestBed.createComponent(S3CredentialsDialogComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    http.expectOne("/api/workflows/s3-credentials").flush([]);
    component.credentialName = "finance";
    component.accessKeyId = "AKIA123";
    component.secretAccessKey = "SECRET123";

    const createPromise = component.createCredential();
    const createReq = http.expectOne("/api/workflows/s3-credentials");
    expect(createReq.request.method).toBe("POST");
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
  });
});
