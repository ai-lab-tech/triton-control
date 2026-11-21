import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestbedHarnessEnvironment } from "@angular/cdk/testing/testbed";
import { MatSelectHarness } from "@angular/material/select/testing";
import { TestBed } from "@angular/core/testing";
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from "@angular/router";
import { of } from "rxjs";
import { AuthStore } from "../../shared/auth/auth.store";
import { HandoffPageComponent } from "./handoff-page.component";

const receipt = {
  id: "receipt-1",
  task: {
    id: "task-1",
    state: "queued",
    missing_inputs: ["evaluation_dataset"],
    blocked_reasons: [],
  },
  request: {
    title: "Iris",
    revision: "2",
    previous_handoff_id: "previous-receipt",
    source: { kind: "s3" },
  },
  source: { key: "iris.onnx", version_id: "v1", file: { digest: "sha256:abc", size: 5 } },
  dataset: null,
  baseline: null,
  created_at: "2026-11-15T12:00:00Z",
};

describe("HandoffPageComponent", () => {
  let http: HttpTestingController;
  async function setup(id: string | null = null, role = "member", requireS3Versioning = true) {
    await TestBed.configureTestingModule({
      imports: [HandoffPageComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap(id ? { handoffId: id } : {})),
            snapshot: { queryParamMap: convertToParamMap({}) },
          },
        },
      ],
    }).compileComponents();
    TestBed.inject(AuthStore).setAuthenticatedUser({
      name: "Scientist",
      email: "scientist@test.invalid",
      role,
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(HandoffPageComponent);
    if (!id && (role === "member" || role === "admin")) {
      http
        .expectOne("/api/preparation/handoff-submission-policy")
        .flush({ require_s3_versioning: requireS3Versioning });
    }
    return fixture;
  }
  afterEach(() => http.verify());

  it("warns about overwritten or deleted files when unversioned inputs are allowed", async () => {
    // Arrange
    const fixture = await setup(null, "member", false);
    http.expectOne("/api/s3-profiles").flush([]);

    // Act
    await fixture.whenStable();
    fixture.detectChanges();

    // Assert
    expect(fixture.nativeElement.textContent).toContain("Unversioned S3 inputs are allowed.");
    expect(fixture.nativeElement.textContent).toContain("engineering may need a new handoff.");
  });

  it("shows the source form with no team picker", async () => {
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "test" }]);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Submit model for preparation");
    expect(fixture.nativeElement.textContent).toContain("installation’s engineering group");
    expect(fixture.nativeElement.querySelector('[name="team"]')).toBeNull();
  });
  it("submits one capture from the rendered handoff form", async () => {
    // Arrange
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "" }]);
    fixture.detectChanges();
    await fixture.whenStable();
    const form = fixture.nativeElement.querySelector("form") as HTMLFormElement;
    const button = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
    expect(button.disabled).toBeTrue();
    const loader = TestbedHarnessEnvironment.loader(fixture);
    const profile = await loader.getHarness(
      MatSelectHarness.with({ selector: '[name="profile"]' }),
    );

    // Act
    await profile.open();
    await profile.clickOptions({ text: /Lab/ });
    for (const [name, value] of [
      ["title", "Street detector"],
      ["source", "street/model.onnx"],
    ]) {
      const input = form.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
      input.value = value;
      input.dispatchEvent(new Event("input"));
    }
    fixture.detectChanges();
    expect(button.disabled).toBeFalse();
    button.click();

    // Assert
    const capture = http.expectOne("/api/preparation/handoff-captures");
    expect(capture.request.method).toBe("POST");
    expect(capture.request.body.title).toBe("Street detector");
    expect(capture.request.body.source.path).toBe("street/model.onnx");
    capture.flush({}, { status: 400, statusText: "Invalid fixture" });
    await fixture.whenStable();
  });
  it("submits explicit inputs and keeps the same identity after a retry", async () => {
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "" }]);
    const page = fixture.componentInstance;
    page.title = "Iris";
    page.profileId = 1;
    page.sourcePath = "iris.onnx";
    page.previousHandoffId = "previous-receipt";
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    let pending = page.submit();
    const first = http.expectOne("/api/preparation/handoff-captures");
    const identity = first.request.body.origin;
    const key = first.request.headers.get("Idempotency-Key");
    expect(first.request.body.dataset).toBeNull();
    expect(first.request.body.previous_handoff_id).toBe("previous-receipt");
    first.flush({ detail: "Please retry" }, { status: 503, statusText: "Unavailable" });
    await pending;
    expect(page.busy()).toBeFalse();
    pending = page.submit();
    const retry = http.expectOne("/api/preparation/handoff-captures");
    expect(retry.request.body.origin).toEqual(identity);
    expect(retry.request.headers.get("Idempotency-Key")).toBe(key);
    expect(retry.request.body.revision).toBe("1");
    expect(retry.request.body.previous_handoff_id).toBe("previous-receipt");
    retry.flush({
      id: "capture-1",
      state: "succeeded",
      message: "",
      result: { handoff_id: receipt.id },
    });
    await pending;
    expect(navigate).toHaveBeenCalledWith(["/preparation/handoffs", "receipt-1"]);
  });
  it("starts an unchanged new submission with a fresh key after cancellation", async () => {
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "" }]);
    const page = fixture.componentInstance;
    page.title = "Iris";
    page.profileId = 1;
    page.sourcePath = "iris.onnx";
    spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    let pending = page.submit();
    const first = http.expectOne("/api/preparation/handoff-captures");
    const key = first.request.headers.get("Idempotency-Key");
    const body = first.request.body;
    first.flush({ id: "old-job", state: "pending", message: "", result: {} });
    await pending;
    pending = page.captureAction("cancel");
    http
      .expectOne("/api/operations/old-job/cancel")
      .flush({ id: "old-job", state: "cancelled", message: "", result: {} });
    await pending;
    fixture.detectChanges();
    const link = Array.from(fixture.nativeElement.querySelectorAll("a")) as HTMLAnchorElement[];
    link.find((item) => item.textContent?.trim() === "New submission")!.click();
    pending = page.submit();
    const fresh = http.expectOne("/api/preparation/handoff-captures");
    expect(fresh.request.body).toEqual(body);
    expect(fresh.request.headers.get("Idempotency-Key")).not.toBe(key);
    fresh.flush({
      id: "new-job",
      state: "succeeded",
      message: "",
      result: { handoff_id: "receipt-1" },
    });
    await pending;
  });

  it("renders missing inputs and the exact pinned object version", async () => {
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain("Evaluation inputs incomplete");
    expect(text).toContain("Evaluation dataset");
    expect(text).toContain("v1");
    expect(text).toContain("task-1");
    expect(
      fixture.nativeElement.querySelector('a[href="/preparation/handoffs/previous-receipt"]'),
    ).not.toBeNull();
  });
  it("does not load source profiles or submit for viewers", async () => {
    const fixture = await setup(null, "viewer");
    await fixture.componentInstance.submit();
    fixture.detectChanges();
    http.expectNone("/api/s3-profiles");
    http.expectNone("/api/preparation/handoff-captures");
    expect(fixture.nativeElement.textContent).toContain("requires member or administrator access");
  });
  it("keeps access errors visible without rendering receipt content", async () => {
    const fixture = await setup("private");
    http
      .expectOne("/api/preparation/handoffs/private")
      .flush(
        { detail: "Preparation task access denied" },
        { status: 403, statusText: "Forbidden" },
      );
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]').textContent).toContain(
      "access denied",
    );
    expect(fixture.componentInstance.receipt()).toBeNull();
  });
  it("claims using the current revision and reloads the receipt", async () => {
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush({
      ...receipt,
      task: { ...receipt.task, revision: 4, assignee_user_id: null },
    });
    await fixture.whenStable();
    const pending = fixture.componentInstance.claimTask();
    const claim = http.expectOne("/api/preparation-tasks/task-1/claim");
    expect(claim.request.body.expected_revision).toBe(4);
    claim.flush({});
    await Promise.resolve();
    http.expectOne("/api/preparation/handoffs/receipt-1").flush({
      ...receipt,
      task: { ...receipt.task, revision: 5, assignee_user_id: 2 },
    });
    await pending;
    expect(fixture.componentInstance.receipt()?.task.assignee_user_id).toBe(2);
  });
  it("previews actual input storage before creating with the chosen capacity", async () => {
    // Arrange
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    const page = fixture.componentInstance;
    spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    // Act
    const preview = page.prepareWorkspace();
    http.expectOne("/api/preparation/tasks/task-1/workspace-sizing").flush({
      input_bytes: 16 * 1073741824,
      recommended_gib: 24,
      maximum_gib: 50,
      existing_storage_size: null,
    });
    await preview;
    // Assert
    expect(page.storageGiB).toBe(24);
    http.expectNone("/api/preparation/tasks/task-1/workspace");
    // Act
    page.storageGiB = 30;
    const pending = page.openWorkspace();
    const request = http.expectOne("/api/preparation/tasks/task-1/workspace");
    const body = request.request.body;
    request.flush({ workspace: { id: 42 } });
    await pending;
    // Assert
    expect(body).toEqual({ workspace: { storage_size: "30Gi" } });
  });
  it("blocks workspace creation when the estimate exceeds the admin maximum", async () => {
    // Arrange
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    const page = fixture.componentInstance;
    page.workspaceSizing.set({
      input_bytes: 40 * 1073741824,
      recommended_gib: 60,
      maximum_gib: 50,
      existing_storage_size: null,
    });
    page.storageGiB = 60;
    // Act
    await page.openWorkspace();
    // Assert
    expect(page.validWorkspaceSize()).toBeFalse();
    http.expectNone("/api/preparation/tasks/task-1/workspace");
  });
  it("opens the task workspace and navigates to its exact instance", async () => {
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    const pending = fixture.componentInstance.openWorkspace();
    http.expectOne("/api/preparation/tasks/task-1/workspace").flush({ workspace: { id: 42 } });
    await pending;
    expect(navigate).toHaveBeenCalledWith(["/workspaces"], { queryParams: { workspace: 42 } });
  });
  it("shows assignment revocation errors and permits retry", async () => {
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    const pending = fixture.componentInstance.openWorkspace();
    http
      .expectOne("/api/preparation/tasks/task-1/workspace")
      .flush(
        { detail: "Only the current eligible assignee may open this task workspace" },
        { status: 403, statusText: "Forbidden" },
      );
    await pending;
    expect(fixture.componentInstance.error()).toContain("current eligible assignee");
    expect(fixture.componentInstance.busy()).toBeFalse();
  });
  it("submits a repository folder while keeping evaluation inputs as individual objects", async () => {
    // Arrange
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "test" }]);
    const page = fixture.componentInstance;
    page.profileId = 1;
    page.title = "Generated Triton repository";
    page.sourceKind = "s3_repository";
    page.sourcePath = " repositories/iris/ ";
    page.datasetPath = "evaluation/data.csv";
    page.baselinePath = "evaluation/baseline.json";
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);

    // Act
    const pending = page.submit();
    const submission = http.expectOne("/api/preparation/handoff-captures");
    const body = submission.request.body;
    submission.flush({
      id: "capture-1",
      state: "succeeded",
      message: "",
      result: { handoff_id: receipt.id },
    });
    await pending;

    // Assert
    expect(body.source).toEqual({
      kind: "s3_repository",
      profile_id: 1,
      path: "repositories/iris/",
    });
    expect(body.dataset).toEqual({ kind: "s3", profile_id: 1, path: "evaluation/data.csv" });
    expect(body.baseline.kind).toBe("s3");
    expect(navigate).toHaveBeenCalledWith(["/preparation/handoffs", "receipt-1"]);
  });

  it("renders repository files with their exact versions and inventory hash", async () => {
    // Arrange
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush({
      ...receipt,
      request: { ...receipt.request, source: { kind: "s3_repository" } },
      source_snapshot: { inventory: "sha256:inventory" },
      source: {
        key: "repositories/iris",
        files: [
          {
            key: "repositories/iris/iris/config.pbtxt",
            version_id: "config-v1",
            file: { path: "iris/config.pbtxt", digest: "sha256:config", size: 12 },
          },
          {
            key: "repositories/iris/iris/1/model.onnx",
            version_id: "model-v2",
            file: { path: "iris/1/model.onnx", digest: "sha256:model", size: 42 },
          },
        ],
      },
    });

    // Act
    await fixture.whenStable();
    fixture.detectChanges();

    // Assert
    const text = fixture.nativeElement.textContent;
    for (const expected of [
      "config-v1",
      "model-v2",
      "sha256:inventory",
      "iris/1/model.onnx",
      "/inputs/verified/source",
    ]) {
      expect(text).toContain(expected);
    }
  });
  it("shows queued capture progress and allows cancellation without holding the submission request", async () => {
    // Arrange
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "" }]);
    const page = fixture.componentInstance;
    page.title = "Large model";
    page.profileId = 1;
    page.sourcePath = "model.onnx";
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    // Act
    const pending = page.submit();
    const request = http.expectOne("/api/preparation/handoff-captures");
    const key = request.request.headers.get("Idempotency-Key");
    request.flush({ id: "job-1", state: "pending", message: "Queued", result: {} });
    await pending;
    fixture.detectChanges();
    // Assert
    expect(key?.length).toBe(64);
    expect(page.busy()).toBeFalse();
    expect(fixture.nativeElement.textContent).toContain("Model capture: pending");
    expect(fixture.nativeElement.textContent).toContain("Cancel capture");
    expect(fixture.nativeElement.querySelector("form")).toBeNull();
    expect(navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { capture: "job-1" } }),
    );
    fixture.destroy();
  });
});
