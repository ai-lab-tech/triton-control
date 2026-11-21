import { TestBed } from "@angular/core/testing";
import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from "@angular/router";
import { of } from "rxjs";
import { EngineeringQueueComponent } from "./engineering-queue.component";
import { PreparationDetailComponent } from "./preparation-detail.component";
import { ExperimentPreparationComponent } from "./experiment-preparation.component";
import { Overview } from "./preparation-api.service";
import { AuthStore } from "../../shared/auth/auth.store";
import { InstallationService } from "../../shared/installation/installation.service";
import { Instance } from "../instances/instances.data";

const entry: Overview = {
  task: {
    id: "task-1",
    title: "Prepare Iris",
    state: "queued",
    revision: 1,
    assigned_team_id: "team-1",
    submitter_user_id: 4,
    assignee_user_id: null,
    source_snapshot_id: null,
    missing_inputs: ["test_data"],
    blocked_reasons: ["missing_input:test_data"],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  source: {
    kind: "s3",
    revision: "v1",
    models: ["iris"],
    snapshot_id: null,
    handoff_id: "handoff-1",
    instance_id: null,
  },
  team_name: "Engineering",
  submitter_name: "Scientist",
  assignee_name: null,
  actions: { claim: true, release: false, request_information: false, open_workspace: false },
  candidates: [],
};
const instance = {
  id: "7",
  name: "Experiment",
  isSelfDeployed: false,
  deploymentRuntime: "external",
  repositoryModels: [{ name: "iris", version: "1", state: "READY", reason: "" }],
} as Instance;
const operation = {
  id: "capture-1",
  kind: "experiment.snapshot",
  target: "7",
  state: "pending",
  message: "Queued",
  cancel_requested: false,
  result: {},
};

describe("Engineering preparation workflow", () => {
  let http: HttpTestingController;
  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ taskId: "task-1" })),
            snapshot: { queryParamMap: convertToParamMap({}) },
          },
        },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(AuthStore).setAuthenticatedUser({
      name: "Engineer",
      email: "engineer@example.test",
      role: "member",
    });
    TestBed.inject(InstallationService).snapshot.set({
      mode: "development",
      capabilities: { development_jobs: true },
      explanation: "Development",
    });
  });
  afterEach(() => http.verify());
  it("shows incomplete tasks and refreshes the winning assignment after a claim conflict", async () => {
    // Arrange
    const fixture = TestBed.createComponent(EngineeringQueueComponent);
    http.expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=all").flush([entry]);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Test data");
    expect(fixture.nativeElement.textContent).toContain("Scientist");
    // Act
    const pending = fixture.componentInstance.claim(entry);
    const claim = http.expectOne("/api/preparation-tasks/task-1/claim");
    expect(claim.request.body).toEqual({ expected_revision: 1 });
    claim.flush({ detail: "Already assigned" }, { status: 409, statusText: "Conflict" });
    await Promise.resolve();
    await Promise.resolve();
    http
      .expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=all")
      .flush([
        { ...entry, assignee_name: "Other engineer", actions: { ...entry.actions, claim: false } },
      ]);
    await pending;
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).toContain("Other engineer");
    expect(fixture.nativeElement.textContent).toContain("Already assigned");
    expect(fixture.nativeElement.textContent).not.toContain("Claim task");
  });
  it("confirms a claim even when the task leaves the unassigned view", async () => {
    const fixture = TestBed.createComponent(EngineeringQueueComponent);
    http.expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=all").flush([entry]);
    await fixture.whenStable();
    fixture.componentInstance.selectView("unassigned");
    http.expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=unassigned").flush([entry]);
    await fixture.whenStable();

    const pending = fixture.componentInstance.claim(entry);
    const task = { ...entry.task, state: "claimed", assignee_user_id: 2, revision: 2 };
    http.expectOne("/api/preparation-tasks/task-1/claim").flush(task);
    await Promise.resolve();
    await Promise.resolve();
    http.expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=unassigned").flush([]);
    await pending;
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain("Assigned to you (Engineer)");
    expect(fixture.nativeElement.textContent).toContain("Open task");
    expect(fixture.nativeElement.textContent).toContain("No unassigned preparation tasks.");

    fixture.componentInstance.selectView("mine");
    http
      .expectOne("/api/preparation-tasks/queue?limit=50&offset=0&view=mine")
      .flush([
        { ...entry, task, assignee_name: "Engineer", actions: { ...entry.actions, claim: false } },
      ]);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector("tbody").textContent).toContain("Engineer");
  });
  it("inspects retained candidates and attempts without presenting task status as deployment status", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    http.expectOne("/api/preparation-tasks/task-1/overview").flush({
      ...entry,
      candidates: [1, 2].map((n) => ({
        installation_id: "dev",
        record_id: `candidate-${n}`,
        candidate_digest: `digest-${n}`,
        created_at: entry.task.created_at,
      })),
    });
    await Promise.resolve();
    await Promise.resolve();
    http.expectOne("/api/preparation-tasks/task-1/attempts?limit=50&offset=0").flush([
      {
        id: "attempt-1",
        created_at: entry.task.created_at,
        attempt: {
          operation: "conversion",
          tool: "converter",
          origin: { record_id: "convert-1" },
          input_inventory: "input",
          output_inventory: "output",
        },
      },
    ]);
    http.expectOne("/api/preparation-tasks/task-1/events?limit=50&offset=0").flush([]);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();
    fixture.detectChanges();
    // Assert
    const text = fixture.nativeElement.textContent;
    expect(text).toContain("candidate-1");
    expect(text).toContain("candidate-2");
    expect(text).toContain("conversion");
    expect(text).toContain("Queued");
    expect(text).toContain(
      "These records do not establish production validation or deployment approval.",
    );
    expect(text).not.toContain("Open task workspace");
    fixture.destroy();
  });
  it("loads build history without changing task status", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    await finishDetailLoad(entry);
    // Act
    const pending = fixture.componentInstance.loadBuilds();
    http.expectOne("/api/builds?task_id=task-1&limit=50&offset=0").flush([
      {
        id: "build-1",
        operation: { state: "succeeded" },
        attempts: [
          { number: 1, state: "failed", final_digest: null },
          { number: 2, state: "succeeded", final_digest: "sha256:retained" },
        ],
      },
    ]);
    await pending;
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).toContain("Attempt 1: failed");
    expect(fixture.nativeElement.textContent).toContain("Attempt 2: succeeded");
    expect(fixture.componentInstance.entry()?.task.state).toBe("queued");
    fixture.destroy();
  });
  it("does not offer task mutations to a read-only submitter", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    http.expectOne("/api/preparation-tasks/task-1/overview").flush({
      ...entry,
      actions: {
        claim: false,
        release: false,
        request_information: false,
        open_workspace: false,
      },
    });
    await Promise.resolve();
    await Promise.resolve();
    http.expectOne("/api/preparation-tasks/task-1/attempts?limit=50&offset=0").flush([]);
    http.expectOne("/api/preparation-tasks/task-1/events?limit=50&offset=0").flush([]);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();
    // Act
    await fixture.componentInstance.action("claim");
    fixture.detectChanges();
    // Assert
    http.expectNone("/api/preparation-tasks/task-1/claim");
    expect(fixture.nativeElement.textContent).not.toContain("Claim task");
  });
  async function finishDetailLoad(value: Overview) {
    http.expectOne("/api/preparation-tasks/task-1/overview").flush(value);
    await Promise.resolve();
    await Promise.resolve();
    http.expectOne("/api/preparation-tasks/task-1/attempts?limit=50&offset=0").flush([]);
    http.expectOne("/api/preparation-tasks/task-1/events?limit=50&offset=0").flush([]);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
  it("requests information with the current revision and retains the assignee", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    const assigned = {
      ...entry,
      task: { ...entry.task, assignee_user_id: 2, state: "claimed", revision: 2 },
      actions: { ...entry.actions, claim: false, release: true, request_information: true },
    };
    await finishDetailLoad(assigned);
    const page = fixture.componentInstance;
    page.reason = "Please supply representative examples";
    page.missing = "test_data, expected_results";
    // Act
    const pending = page.action("request-information");
    const request = http.expectOne("/api/preparation-tasks/task-1/request-information");
    expect(request.request.body).toEqual({
      expected_revision: 2,
      reason: page.reason,
      missing_inputs: ["test_data", "expected_results"],
    });
    request.flush({ ...assigned.task, state: "awaiting_information", revision: 3 });
    await Promise.resolve();
    await Promise.resolve();
    await finishDetailLoad({
      ...assigned,
      task: { ...assigned.task, state: "awaiting_information", revision: 3 },
    });
    await pending;
    // Assert
    expect(page.entry()?.task.state).toBe("awaiting_information");
    expect(page.entry()?.task.assignee_user_id).toBe(2);
    fixture.destroy();
  });
  it("releases an assignment and refreshes available actions", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    await finishDetailLoad({
      ...entry,
      actions: { ...entry.actions, claim: false, release: true },
    });
    // Act
    const pending = fixture.componentInstance.action("release-claim");
    const request = http.expectOne("/api/preparation-tasks/task-1/release-claim");
    expect(request.request.body.expected_revision).toBe(1);
    request.flush(entry.task);
    await Promise.resolve();
    await Promise.resolve();
    await finishDetailLoad(entry);
    await pending;
    // Assert
    expect(fixture.componentInstance.entry()?.actions.claim).toBeTrue();
    fixture.destroy();
  });
  it("uses workspace sizing and navigates to the exact workspace", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    await finishDetailLoad({ ...entry, actions: { ...entry.actions, open_workspace: true } });
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    const page = fixture.componentInstance;
    // Act
    let pending = page.prepareWorkspace();
    http
      .expectOne("/api/preparation/tasks/task-1/workspace-sizing")
      .flush({ input_bytes: 10, recommended_gib: 6, maximum_gib: 50, existing_storage_size: null });
    await pending;
    page.storageGiB = 5;
    await page.openWorkspace();
    http.expectNone("/api/preparation/tasks/task-1/workspace");
    page.storageGiB = 10;
    pending = page.openWorkspace();
    const request = http.expectOne("/api/preparation/tasks/task-1/workspace");
    expect(request.request.body).toEqual({ workspace: { storage_size: "10Gi" } });
    request.flush({ workspace: { id: 42 } });
    await pending;
    // Assert
    expect(navigate).toHaveBeenCalledWith(["/workspaces"], { queryParams: { workspace: 42 } });
    fixture.destroy();
  });
  it("renders immutable snapshot gaps and links back to the original experiment", async () => {
    // Arrange
    const fixture = TestBed.createComponent(PreparationDetailComponent);
    const snapshotEntry = {
      ...entry,
      source: { ...entry.source, kind: "experiment", snapshot_id: "snapshot-1", instance_id: 7 },
    };
    const pending = finishDetailLoad(snapshotEntry);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    http.expectOne("/api/preparation/snapshots/snapshot-1").flush({
      id: "snapshot-1",
      manifest_digest: "digest",
      manifest: {
        captured_at: entry.task.created_at,
        previous_snapshot_id: null,
        gaps: ["runtime_changes_unknown"],
        request: { expected_behavior: null, acceptance_criteria: null },
        inventory: { files: [{ path: "iris/1/model.onnx", size: 10, digest: "checksum" }] },
        workspace_inventory: { files: [] },
      },
    });
    await pending;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).toContain("runtime_changes_unknown");
    expect(fixture.nativeElement.textContent).toContain("iris/1/model.onnx");
    expect(fixture.nativeElement.querySelector('a[href="/instances/7"]')).not.toBeNull();
    fixture.destroy();
  });
  function captureFixture() {
    const fixture = TestBed.createComponent(ExperimentPreparationComponent);
    fixture.componentRef.setInput("instance", instance);
    fixture.detectChanges();
    fixture.componentInstance.profileId = 1;
    fixture.componentInstance.prefix = "models";
    spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    return fixture;
  }
  for (const kind of ["hf", "manual", "example"] as const) {
    it(`submits the ${kind} workspace source from the capture form`, async () => {
      // Arrange
      const fixture = captureFixture();
      const page = fixture.componentInstance;
      page.expanded.set(true);
      page.profileId = null;
      page.prefix = "";
      fixture.detectChanges();
      await fixture.whenStable();
      const form = fixture.nativeElement.querySelector("form") as HTMLFormElement;
      const kindInput = form.querySelector<HTMLSelectElement>('select[name="sourceKind"]')!;
      kindInput.value = kind;
      kindInput.dispatchEvent(new Event("change"));
      fixture.detectChanges();
      await fixture.whenStable();
      const fields: Record<string, string> = {
        workspaceId: "7",
        workspaceFiles: "repo/iris/config.pbtxt",
        modelRoot: "repo",
      };
      if (kind === "manual") fields["author"] = "Ada";
      if (kind === "example") fields["project_path"] = "examples/street";
      if (kind === "hf") {
        fields["revision"] = "main";
        fields["checkpoints"] = "iris/1/model.bin";
      }
      for (const [name, value] of Object.entries(fields)) {
        const field = form.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
        field.value = value;
        field.dispatchEvent(new Event("input"));
      }
      fixture.detectChanges();

      // Act
      form.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();

      // Assert
      const capture = http.expectOne("/api/instances/7/preparation-requests");
      expect(capture.request.body.source.kind).toBe(kind);
      if (kind === "manual") expect(capture.request.body.source.author).toBe("Ada");
      if (kind === "example")
        expect(capture.request.body.source.project_path).toBe("examples/street");
      if (kind === "hf") {
        expect(capture.request.body.source.revision).toBe("main");
        expect(capture.request.body.source.checkpoint_files).toEqual(["iris/1/model.bin"]);
      }
      expect(capture.request.body.source.workspace_model_root).toBe("repo");
      expect(capture.request.body.workspace).toEqual({
        workspace_id: 7,
        files: ["repo/iris/config.pbtxt"],
      });
      capture.flush({}, { status: 400, statusText: "Invalid fixture" });
      await fixture.whenStable();
      fixture.destroy();
    });
  }

  it("sends the snapshot contract, retains idempotency after a lost response and never mutates the experiment", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    const before = JSON.stringify(instance);
    // Act
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    expect(first.request.body.models).toEqual([{ name: "iris", versions: ["1"] }]);
    first.flush({}, { status: 502, statusText: "Lost response" });
    await pending;
    pending = page.submit();
    const retry = http.expectOne("/api/instances/7/preparation-requests");
    expect(retry.request.headers.get("Idempotency-Key")).toBe(key);
    retry.flush({ operation: { ...operation, state: "succeeded", result: { task_id: "task-1" } } });
    await pending;
    page.expanded.set(true);
    fixture.detectChanges();
    // Assert
    expect(JSON.stringify(instance)).toBe(before);
    expect(fixture.nativeElement.querySelector('a[href="/instances/7"]')).not.toBeNull();
    expect(
      fixture.nativeElement.querySelector('a[href="/preparation/tasks/task-1"]'),
    ).not.toBeNull();
    expect(
      http.match((request) => ["PUT", "PATCH", "DELETE"].includes(request.method)).length,
    ).toBe(0);
    fixture.destroy();
  });
  it("blocks new submissions while resuming status and retries the existing snapshot", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    // Act
    let pending = page.monitor("capture-1");
    await page.submit();
    http.expectNone("/api/instances/7/preparation-requests");
    http.expectOne("/api/operations/capture-1").flush({ ...operation, state: "failed" });
    await pending;
    pending = page.act("retry");
    http
      .expectOne("/api/operations/capture-1/retry")
      .flush({ ...operation, state: "succeeded", result: { task_id: "task-1" } });
    await pending;
    // Assert
    expect(page.taskId()).toBe("task-1");
    expect(page.busy()).toBeFalse();
    fixture.destroy();
  });
  it("loads named repository profiles when opening the capture form", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    // Act
    const pending = page.begin();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Research models", bucket: "models", prefix: "" }]);
    await pending;
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).toContain("Research models");
    fixture.destroy();
  });
  it("uses a fresh capture key after explicit cancellation", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    first.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Act
    page.newCapture();
    pending = page.submit();
    const next = http.expectOne("/api/instances/7/preparation-requests");
    const nextKey = next.request.headers.get("Idempotency-Key");
    next.flush({
      operation: {
        ...operation,
        id: "capture-2",
        state: "succeeded",
        result: { task_id: "task-2" },
      },
    });
    await pending;
    // Assert
    expect(nextKey).not.toBe(key);
    fixture.destroy();
  });
  it("hides and rejects capture in production and for viewers", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    TestBed.inject(InstallationService).snapshot.set({
      mode: "production",
      capabilities: { development_jobs: false },
      explanation: "Production",
    });
    // Act
    await page.submit();
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).not.toContain("Prepare for production");
    http.expectNone("/api/instances/7/preparation-requests");
    // Act
    TestBed.inject(InstallationService).snapshot.set({
      mode: "development",
      capabilities: { development_jobs: true },
      explanation: "Development",
    });
    TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Viewer", role: "viewer" });
    await page.submit();
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).not.toContain("Prepare for production");
    http.expectNone("/api/instances/7/preparation-requests");
    fixture.destroy();
  });
  it("pins managed capture to observed deployment identity", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    fixture.componentRef.setInput("instance", {
      ...instance,
      isSelfDeployed: true,
      deploymentRuntime: "kubernetes",
    });
    page.profileId = null;
    page.prefix = "";
    // Act
    const pending = page.submit();
    http.expectOne("/api/instances/7/pods").flush({
      managed: true,
      deployment: { uid: "deployment-uid", generation: 3, stale: false },
      pods: [],
    });
    await Promise.resolve();
    await Promise.resolve();
    const request = http.expectOne("/api/instances/7/preparation-requests");
    expect(request.request.body.deployment_uid).toBe("deployment-uid");
    expect(request.request.body.expected_generation).toBe(3);
    request.flush({
      operation: { ...operation, state: "succeeded", result: { task_id: "task-1" } },
    });
    await pending;
    fixture.destroy();
  });

  it("replays the original managed request without observing a later rollout", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    fixture.componentRef.setInput("instance", {
      ...instance,
      isSelfDeployed: true,
      deploymentRuntime: "kubernetes",
    });
    page.profileId = null;
    page.prefix = "";
    let pending = page.submit();
    http
      .expectOne("/api/instances/7/pods")
      .flush({ managed: true, deployment: { uid: "original", generation: 3, stale: false } });
    await Promise.resolve();
    await Promise.resolve();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    const body = first.request.body;
    first.flush({}, { status: 502, statusText: "Lost response" });
    await pending;
    // Act
    pending = page.submit();
    const retry = http.expectOne("/api/instances/7/preparation-requests");
    const retryKey = retry.request.headers.get("Idempotency-Key");
    const retryBody = retry.request.body;
    retry.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    fixture.destroy();
    // Assert
    http.expectNone("/api/instances/7/pods");
    expect(retryKey).toBe(key);
    expect(retryBody).toEqual(body);
    expect(retryBody.expected_generation).toBe(3);
  });
  it("retains the memory request when session storage writes fail", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    spyOn(Storage.prototype, "setItem").and.throwError("QuotaExceededError");
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    first.flush({}, { status: 502, statusText: "Lost response" });
    await pending;
    // Act
    pending = page.submit();
    const retry = http.expectOne("/api/instances/7/preparation-requests");
    const retryKey = retry.request.headers.get("Idempotency-Key");
    retry.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    fixture.destroy();
    // Assert
    expect(retryKey).toBe(key);
  });
  it("restores the saved request after reload instead of the current instance defaults", async () => {
    // Arrange
    const saved = {
      title: "Original selection",
      models: [{ name: "iris", versions: ["1", "2"] }],
      repository: { profile_id: 9, prefix: "original" },
      expected_behavior: "Original behavior",
      acceptance_criteria: null,
      deployment_uid: "before-rollout",
      expected_generation: 3,
    };
    sessionStorage.setItem(
      "preparation-capture:engineer@example.test:7",
      JSON.stringify({ key: "original-key", payload: JSON.stringify(saved) }),
    );
    const fixture = TestBed.createComponent(ExperimentPreparationComponent);
    fixture.componentRef.setInput("instance", {
      ...instance,
      isSelfDeployed: true,
      deploymentRuntime: "kubernetes",
    });
    spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    fixture.detectChanges();
    const page = fixture.componentInstance;
    // Act
    const pending = page.submit();
    const request = http.expectOne("/api/instances/7/preparation-requests");
    const body = request.request.body;
    const key = request.request.headers.get("Idempotency-Key");
    request.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Assert
    expect(page.title).toBe(saved.title);
    expect(page.models).toBe("iris:1\niris:2");
    expect(body).toEqual(saved);
    expect(key).toBe("original-key");
    http.expectNone("/api/instances/7/pods");
    fixture.destroy();
  });
  it("groups and deduplicates versions without dropping other models", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    page.models = "iris:1\niris:2\niris:1\nother:3";
    // Act
    const pending = page.submit();
    const request = http.expectOne("/api/instances/7/preparation-requests");
    const models = request.request.body.models;
    request.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Assert
    expect(models).toEqual([
      { name: "iris", versions: ["1", "2"] },
      { name: "other", versions: ["3"] },
    ]);
    fixture.destroy();
  });
  it("prefills every observed model version", () => {
    // Arrange
    const fixture = TestBed.createComponent(ExperimentPreparationComponent);
    fixture.componentRef.setInput("instance", {
      ...instance,
      repositoryModels: [
        { name: "iris", version: "1" },
        { name: "iris", version: "2" },
        { name: "iris", version: "1" },
      ],
    });
    // Act
    fixture.detectChanges();
    // Assert
    expect(fixture.componentInstance.models).toBe("iris:1\niris:2");
    fixture.destroy();
  });
  it("does not revive a completed request when storage removal fails", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    first.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    spyOn(Storage.prototype, "removeItem").and.throwError("Storage unavailable");
    // Act
    page.newCapture();
    pending = page.submit();
    const next = http.expectOne("/api/instances/7/preparation-requests");
    const nextKey = next.request.headers.get("Idempotency-Key");
    next.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Assert
    expect(nextKey).not.toBe(key);
    fixture.destroy();
  });

  it("allows correcting an initial admission rejection", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const firstKey = first.request.headers.get("Idempotency-Key");
    first.flush({ detail: "Invalid repository" }, { status: 400, statusText: "Bad Request" });
    await pending;
    page.prefix = "corrected";
    // Act
    pending = page.submit();
    const corrected = http.expectOne("/api/instances/7/preparation-requests");
    const key = corrected.request.headers.get("Idempotency-Key");
    const prefix = corrected.request.body.repository.prefix;
    corrected.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Assert
    expect(key).not.toBe(firstKey);
    expect(prefix).toBe("corrected");
    fixture.destroy();
  });
  it("retains an uncertain request through a later authorization rejection", async () => {
    // Arrange
    const fixture = captureFixture();
    const page = fixture.componentInstance;
    let pending = page.submit();
    const first = http.expectOne("/api/instances/7/preparation-requests");
    const key = first.request.headers.get("Idempotency-Key");
    first.flush({}, { status: 502, statusText: "Lost response" });
    await pending;
    // Act
    pending = page.submit();
    http
      .expectOne("/api/instances/7/preparation-requests")
      .flush({}, { status: 403, statusText: "Forbidden" });
    await pending;
    pending = page.submit();
    const recovered = http.expectOne("/api/instances/7/preparation-requests");
    const recoveredKey = recovered.request.headers.get("Idempotency-Key");
    recovered.flush({ operation: { ...operation, state: "cancelled" } });
    await pending;
    // Assert
    expect(recoveredKey).toBe(key);
    fixture.destroy();
  });
});
