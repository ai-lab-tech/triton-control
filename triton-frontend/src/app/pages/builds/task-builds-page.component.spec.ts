import { TestBed } from "@angular/core/testing";
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from "@angular/router";
import { BehaviorSubject, Subject, of, throwError } from "rxjs";
import {
  BuildsApiService,
  BuildLogs,
  TaskBuild,
  buildLabel,
  imageReference,
} from "./builds-api.service";
import { TaskBuildsPageComponent } from "./task-builds-page.component";

describe("Task serving builds", () => {
  let api: jasmine.SpyObj<BuildsApiService>;
  let params: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  const taskId = "task-one";
  const failed: TaskBuild = {
    id: "a".repeat(32),
    task_id: taskId,
    operation: { state: "failed", cancel_requested: false, message: "Build failed", result: null },
    attempts: [
      {
        number: 1,
        state: "failed",
        destination: "registry/model:attempt-1",
        context_id: "b".repeat(32),
        final_digest: null,
        cleaned: true,
        created_at: "2026-01-01T12:00:00Z",
        finished_at: "2026-01-01",
      },
    ],
  };
  beforeEach(async () => {
    api = jasmine.createSpyObj("BuildsApiService", ["options", "list", "action", "logs"]);
    api.options.and.returnValue(
      of({ task_id: taskId, repositories: ["registry/model"], max_context_bytes: 10000 }),
    );
    api.list.and.returnValue(of([failed]));
    params = new BehaviorSubject(convertToParamMap({ taskId }));
    await TestBed.configureTestingModule({
      imports: [TaskBuildsPageComponent],
      providers: [
        provideRouter([]),
        { provide: BuildsApiService, useValue: api },
        { provide: ActivatedRoute, useValue: { paramMap: params } },
      ],
    }).compileComponents();
  });
  it("shows failed attempts and offers a distinct retry without a digest", async () => {
    // Arrange
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    // Act
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.textContent).toContain("Retry build");
    expect(fixture.nativeElement.textContent).toContain("Attempt 1");
    expect(fixture.nativeElement.textContent).not.toContain("Candidate runtime image:");
    expect(fixture.nativeElement.textContent).not.toContain("Build again with saved files");
  });
  it("retries the failed build using its existing build ID", async () => {
    api.action.and.returnValue(of(failed));
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    fixture.detectChanges();

    const buttons: HTMLButtonElement[] = Array.from(
      fixture.nativeElement.querySelectorAll("button"),
    );
    buttons.find((button) => button.textContent?.includes("Retry build"))!.click();
    await fixture.whenStable();

    expect(api.action).toHaveBeenCalledOnceWith(failed.id, "retry");
  });
  it("links back to the task when the build page was opened directly", async () => {
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    await fixture.whenStable();
    fixture.detectChanges();

    fixture.nativeElement.querySelector("a").click();

    expect(navigate).toHaveBeenCalledWith(["/preparation/tasks", taskId]);
  });
  it("production or denied options hide build submission", async () => {
    // Arrange
    api.options.and.returnValue(throwError(() => ({ status: 403 })));
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    // Act
    fixture.detectChanges();
    // Assert
    expect(fixture.nativeElement.querySelector("form")).toBeNull();
    expect(fixture.nativeElement.textContent).not.toContain("Retry build");
  });
  it("lost task access clears previously visible results and logs", async () => {
    // Arrange
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    api.list.and.returnValue(throwError(() => ({ status: 403 })));
    // Act
    await fixture.componentInstance.refresh();
    fixture.detectChanges();
    // Assert
    expect(fixture.componentInstance.builds()).toEqual([]);
    expect(fixture.componentInstance.options()).toBeNull();
    expect(fixture.nativeElement.querySelector("article")).toBeNull();
  });
  it("does not mutate a build from another task", async () => {
    // Arrange
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    // Act
    await fixture.componentInstance.action({ ...failed, task_id: "other" }, "retry");
    // Assert
    expect(api.action).not.toHaveBeenCalled();
  });
  it("uses the reported digest and preserves a registry port", () => {
    // Arrange
    const attempt = {
      ...failed.attempts[0],
      state: "succeeded",
      destination: "registry:5000/model:attempt-2",
      final_digest: "sha256:" + "a".repeat(64),
    };
    // Act
    const image = imageReference(attempt);
    // Assert
    expect(image).toBe("registry:5000/model@" + attempt.final_digest);
    expect(imageReference({ ...attempt, state: "failed" })).toBeNull();
  });
  it("shows pushing only when the backend reports that phase", () => {
    // Arrange
    const build = {
      ...failed,
      operation: { ...failed.operation, state: "pending", result: { phase: "pushing" } },
      attempts: [],
    };
    // Act
    const label = buildLabel(build);
    // Assert
    expect(label).toBe("Pushing");
  });
  it("does not restore in-flight logs after task access is revoked", async () => {
    // Arrange
    const fixture = TestBed.createComponent(TaskBuildsPageComponent);
    await fixture.whenStable();
    const pending = new Subject<BuildLogs>();
    api.logs.and.returnValue(pending);
    const loading = fixture.componentInstance.showLogs(failed, 1);
    api.list.and.returnValue(throwError(() => ({ status: 403 })));
    // Act
    await fixture.componentInstance.refresh();
    pending.next({
      attempt: 1,
      output: "old logs",
      expired: false,
      truncated: false,
      retained: true,
    });
    pending.complete();
    await loading;
    // Assert
    expect(fixture.componentInstance.log()).toBeNull();
  });
});
