import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { ComponentFixture, TestBed } from "@angular/core/testing";
import { provideNoopAnimations } from "@angular/platform-browser/animations";
import { WorkloadPanelComponent } from "./workload-panel.component";
import { InstancePods, Pod } from "./workload.types";

const pod: Pod = {
  uid: "uid-a",
  name: "pod-a",
  phase: "Running",
  ready: false,
  restarts: 2,
  terminating: false,
  current_revision: true,
  reason: "ImagePullBackOff",
  created_at: "2026-01-01T00:00:00Z",
  conditions: [],
  containers: [
    {
      name: "download",
      kind: "init",
      ready: false,
      restart_count: 2,
      state: "waiting",
      reason: "ImagePullBackOff",
    },
    { name: "triton", kind: "regular", ready: false, restart_count: 0, state: "unknown" },
  ],
};
const snapshot: InstancePods = {
  managed: true,
  endpoint_live: true,
  endpoint_ready: false,
  pods: [pod],
  deployment: {
    uid: "deployment",
    name: "serving",
    generation: 3,
    observed_generation: 2,
    stale: true,
    desired: 3,
    updated: 1,
    ready: 0,
    available: 0,
    conditions: [],
  },
};

describe("WorkloadPanelComponent", () => {
  let fixture: ComponentFixture<WorkloadPanelComponent>;
  let component: WorkloadPanelComponent;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [WorkloadPanelComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(WorkloadPanelComponent);
    fixture.componentRef.setInput("instanceId", "47");
    fixture.componentRef.setInput("active", false);
    fixture.detectChanges();
    component = fixture.componentInstance;
  });
  afterEach(() => {
    fixture.destroy();
    http.verify();
  });

  async function refresh(value = snapshot) {
    const pending = component.refresh();
    http.expectOne("/api/instances/47/pods").flush(value);
    await pending;
    fixture.detectChanges();
  }

  it("shows init failure, counts and stale observation and emits the exact UID", async () => {
    await refresh();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain("ImagePullBackOff");
    expect(text).toContain("Waiting for Kubernetes");
    expect(text).toContain("download");
    expect(text).toContain("Desired:");
    const emit = spyOn(component.viewLogs, "emit");
    const button = [...fixture.nativeElement.querySelectorAll("button")].find(
      (b: HTMLButtonElement) => b.textContent?.includes("View logs"),
    );
    button.click();
    expect(emit).toHaveBeenCalledWith("uid-a");
  });

  it("preserves removed UID and refuses to substitute a same-name replacement", async () => {
    await refresh();
    component.view = "logs";
    await refresh({ ...snapshot, pods: [{ ...pod, uid: "uid-b" }] });
    expect(component.selectedUid()).toBe("uid-a");
    expect(component.removed()).toBeTrue();
    await component.readLogs();
    http.expectNone((r) => r.url.endsWith("pod-logs"));
    expect(fixture.nativeElement.textContent).toContain("removed or replaced");
    component.selectPod("uid-b");
    expect(component.removed()).toBeFalse();
  });

  it("requests selected init previous logs with bounded parameters and displays partial failures", async () => {
    await refresh();
    component.view = "logs";
    component.sinceMinutes = 15;
    component.previous.set(true);
    component.selectedContainer.set("download");
    const pending = component.readLogs();
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    expect(request.request.params.get("pod_uid")).toBe("uid-a");
    expect(request.request.params.get("container")).toBe("download");
    expect(request.request.params.get("previous")).toBe("true");
    expect(request.request.params.get("byte_limit")).toBe("262144");
    expect(request.request.params.has("since")).toBeTrue();
    request.flush({
      truncated: true,
      streams: [
        {
          pod_uid: "uid-a",
          pod_name: "pod-a",
          container: "download",
          previous: true,
          text: "2026-01-01T00:00:00Z downloaded",
          truncated: false,
        },
        {
          pod_uid: "uid-a",
          pod_name: "pod-a",
          container: "triton",
          previous: true,
          text: "",
          error: "No previous logs",
          truncated: false,
        },
      ],
    });
    await pending;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("No previous logs");
    expect(component.output()).toContain("uid-a");
    expect(component.output()).toContain("2026-01-01T00:00:00Z");
    await component.readLogs();
    http.expectNone((r) => r.url.endsWith("pod-logs"));
  });

  it("keeps all-pod selection across refresh and omits UID", async () => {
    await refresh();
    component.selectPod("");
    await refresh();
    expect(component.selectedUid()).toBe("");
    const pending = component.readLogs();
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    expect(request.request.params.has("pod_uid")).toBeFalse();
    request.flush({ streams: [], truncated: false });
    await pending;
  });

  it("clears previous content on permission denial", async () => {
    await refresh();
    const pending = component.refresh();
    http
      .expectOne("/api/instances/47/pods")
      .flush({ detail: "No instance access" }, { status: 403, statusText: "Forbidden" });
    await pending;
    fixture.detectChanges();
    expect(component.state()).toBeNull();
    expect(component.logs()).toBeNull();
    expect(fixture.nativeElement.textContent).toContain("No instance access");
  });

  it("discards in-flight logs after a pod refresh denies access", async () => {
    // Arrange
    await refresh();
    const logRead = component.readLogs();
    const logRequest = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    const stateRead = component.refresh();
    // Act
    http
      .expectOne("/api/instances/47/pods")
      .flush({ detail: "No instance access" }, { status: 403, statusText: "Forbidden" });
    await stateRead;
    logRequest.flush({ streams: [{ text: "stale output" }], truncated: false });
    await logRead;
    // Assert
    expect(component.logs()).toBeNull();
    expect(component.lastLoaded()).toBeNull();
    expect(component.logsLoading()).toBeFalse();
    expect(component.pendingRead()).toBeFalse();
    expect(component.canRead()).toBeFalse();
  });

  it("does not expose stale logs from an in-flight previous selection", async () => {
    await refresh();
    const pending = component.readLogs();
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    component.selectPod("new-uid");
    request.flush({ streams: [{ pod_uid: "uid-a", text: "old selection" }], truncated: false });
    await pending;
    expect(component.logs()).toBeNull();
  });

  it("reselects the exact requested UID even when View logs is clicked again for the same pod", async () => {
    await refresh();
    fixture.componentRef.setInput("initialPodUid", "uid-a");
    fixture.componentRef.setInput("selectionRevision", 1);
    fixture.detectChanges();
    component.selectPod("");
    fixture.componentRef.setInput("selectionRevision", 2);
    fixture.detectChanges();
    expect(component.selectedUid()).toBe("uid-a");
  });

  it("limits filtering to loaded lines and does not fetch more data", () => {
    component.filter = "error";
    expect(component.filtered("timestamp hello\ntimestamp ERROR failed")).toBe(
      "timestamp ERROR failed",
    );
    http.expectNone(() => true);
  });

  it("opens the available tail and reports loaded lines independently of the local filter", async () => {
    await refresh();
    component.view = "logs";
    const pending = component.readLogs();
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    expect(request.request.params.has("since")).toBeFalse();
    request.flush({
      streams: [
        {
          pod_uid: "uid-a",
          pod_name: "pod-a",
          container: "triton",
          previous: false,
          text: "first line\nsecond line\n",
          truncated: false,
        },
      ],
      truncated: false,
    });
    await pending;
    component.filter = "second";
    fixture.detectChanges();
    expect(component.loadedLineCount()).toBe(2);
    expect(component.matchingLineCount()).toBe(1);
    expect(fixture.nativeElement.textContent).toContain("2 lines loaded");
    expect(component.lastLoaded()).not.toBeNull();
    component.clearLogs();
    expect(component.lastLoaded()).toBeNull();
  });

  it("distinguishes empty container output from a filter with no matches", async () => {
    await refresh();
    component.view = "logs";
    component.logs.set({
      streams: [
        {
          pod_uid: "uid-a",
          pod_name: "pod-a",
          container: "triton",
          previous: false,
          text: "",
          truncated: false,
        },
      ],
      truncated: false,
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("No output in this time range");
    component.filter = "unmatched";
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("No lines match your filter");
    http.expectNone(() => true);
  });

  it("selects the serving container instead of the completed init container", async () => {
    await refresh();
    expect(component.selectedUid()).toBe("uid-a");
    expect(component.selectedContainer()).toBe("triton");
    component.view = "logs";
    fixture.detectChanges();
    const active = fixture.nativeElement.querySelector(".container-resource.active-resource");
    expect(active.textContent).toContain("triton");
    expect(fixture.nativeElement.textContent).toContain("ImagePullBackOff");
  });

  it("preserves an explicit failing init-container handoff", async () => {
    fixture.componentRef.setInput("initialPodUid", "uid-a");
    fixture.componentRef.setInput("initialContainer", "download");
    fixture.componentRef.setInput("selectionRevision", 1);
    fixture.detectChanges();
    await refresh();
    expect(component.selectedContainer()).toBe("download");
  });

  it("loads a manually selected container even while automatic refresh is off", async () => {
    await refresh();
    component.view = "logs";
    component.active = true;
    component.setAutoRefresh(false);
    component.previous.set(true);
    component.selectContainer("uid-a", "download");
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    expect(request.request.params.get("container")).toBe("download");
    expect(request.request.params.get("previous")).toBe("false");
    request.flush({ streams: [], truncated: false });
    await Promise.resolve();
    expect(component.autoRefresh()).toBeFalse();
  });

  it("freezes the current snapshot when switching off automatic refresh during an in-flight update", async () => {
    await refresh();
    const snapshot = { streams: [], truncated: false };
    component.logs.set(snapshot);
    const pending = component.readLogs();
    const request = http.expectOne((r) => r.url.endsWith("/pod-logs"));
    component.setAutoRefresh(false);
    request.flush({ streams: [{ text: "late output" }], truncated: false });
    await pending;
    expect(component.logs()).toBe(snapshot);
    expect(component.autoRefresh()).toBeFalse();
  });

  it("keeps static output and selection when a rollout replaces the pod", async () => {
    await refresh();
    component.setAutoRefresh(false);
    const logSnapshot = { streams: [], truncated: false };
    component.logs.set(logSnapshot);
    await refresh({ ...snapshot, pods: [{ ...pod, uid: "replacement" }] });
    expect(component.selectedUid()).toBe("uid-a");
    expect(component.unavailable()).toBeTrue();
    expect(component.logs()).toBe(logSnapshot);
  });

  it("queues a throttled selection for retry instead of leaving a static view empty", async () => {
    await refresh();
    component.setAutoRefresh(false);
    const pending = component.readLogs();
    http
      .expectOne((r) => r.url.endsWith("/pod-logs"))
      .flush(
        { detail: "Wait before refreshing" },
        { status: 429, statusText: "Too Many Requests" },
      );
    await pending;
    expect(component.pendingRead()).toBeTrue();
    expect(component.logError()).toBe("");
    expect(component.canRead()).toBeFalse();
    expect(component.autoRefresh()).toBeFalse();
  });

  it("defaults to manual refresh and does not change the setting during reading or searching", async () => {
    await refresh();
    component.view = "logs";
    fixture.detectChanges();
    expect(component.autoRefresh()).toBeFalse();
    component.setAutoRefresh(true);
    const viewport = fixture.nativeElement.querySelector(".log-viewport");
    viewport.dispatchEvent(new Event("pointerdown"));
    viewport.dispatchEvent(new Event("wheel"));
    viewport.dispatchEvent(new KeyboardEvent("keydown", { key: "Home" }));
    fixture.nativeElement.querySelector(".log-search input").dispatchEvent(new Event("focus"));
    expect(component.autoRefresh()).toBeTrue();
    http.expectNone((r) => r.url.endsWith("/pod-logs"));
  });

  it("loads the initial log snapshot with auto-refresh off", async () => {
    component.view = "logs";
    component.active = true;
    const pending = component.refresh();
    http.expectOne("/api/instances/47/pods").flush(snapshot);
    await Promise.resolve();
    http.expectOne((r) => r.url.endsWith("/pod-logs")).flush({ streams: [], truncated: false });
    await pending;
    expect(component.logs()).not.toBeNull();
    expect(component.autoRefresh()).toBeFalse();
    const statusRefresh = component.refresh();
    http.expectOne("/api/instances/47/pods").flush(snapshot);
    await statusRefresh;
    http.expectNone((r) => r.url.endsWith("/pod-logs"));
  });

  it("shows endpoint health only for an external endpoint", async () => {
    await refresh({ ...snapshot, managed: false, deployment: null, pods: [] });
    expect(fixture.nativeElement.textContent).toContain("External endpoint");
    expect(fixture.nativeElement.querySelector("table")).toBeNull();
    await component.readLogs();
    http.expectNone((r) => r.url.endsWith("pod-logs"));
  });
});
