import { ComponentFixture, TestBed, fakeAsync, flushMicrotasks, tick } from "@angular/core/testing";
import { ActivatedRoute, convertToParamMap } from "@angular/router";
import { BehaviorSubject, Subject, of, throwError } from "rxjs";

import {
  InstancesService,
  ModelPerfRunResponse,
  ModelPerfStatusResponse,
  PerfAnalyzersService,
} from "../../../api/generated/index";
import { InstanceModelProfilePageComponent } from "./instance-model-profile-page.component";

describe("Model Perf jobs", () => {
  let fixture: ComponentFixture<InstanceModelProfilePageComponent>;
  let component: InstanceModelProfilePageComponent;
  let api: {
    getModelPerfStatus: jasmine.Spy;
    startModelPerf: jasmine.Spy;
    stopModelPerf: jasmine.Spy;
  };
  const routeParams = (name: string, version = "1") =>
    convertToParamMap({ id: "7", modelName: name, version });
  let params: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  const idle = (): ModelPerfStatusResponse => ({
    default_image: "sdk:test",
    latest_result: { found: false },
  });
  const run = (state = "running", version = "1"): ModelPerfRunResponse => ({
    id: "run-a",
    instance_id: 7,
    model_name: "model-a",
    model_version: version,
    state,
    message: state,
    image: "sdk:test",
    batch_size: 1,
    concurrency_range: "1",
    measurement_request_count: 50,
    created_at: "2026-09-14T10:00:00",
    output: "",
  });

  beforeEach(async () => {
    params = new BehaviorSubject(routeParams("model-a"));
    api = jasmine.createSpyObj("PerfAnalyzersService", [
      "getModelPerfStatus",
      "startModelPerf",
      "stopModelPerf",
    ]);
    api.getModelPerfStatus.and.returnValue(of(idle()));
    api.startModelPerf.and.returnValue(of(run("creating")));
    api.stopModelPerf.and.returnValue(of(run("stopping")));
    await TestBed.configureTestingModule({
      imports: [InstanceModelProfilePageComponent],
      providers: [
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: params.value }, paramMap: params },
        },
        { provide: PerfAnalyzersService, useValue: api },
        {
          provide: InstancesService,
          useValue: { getInstanceApiInstancesInstanceIdGet: () => of({ name: "test" }) },
        },
      ],
    })
      .overrideComponent(InstanceModelProfilePageComponent, { set: { template: "" } })
      .compileComponents();
  });

  function mount(): void {
    fixture = TestBed.createComponent(InstanceModelProfilePageComponent);
    component = fixture.componentInstance;
  }

  afterEach(() => fixture.destroy());

  it("starts from the model tab without a global installation", fakeAsync(() => {
    mount();
    flushMicrotasks();
    expect(component.canRun()).toBeTrue();
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run("creating") }));
    void component.runProfiler();
    flushMicrotasks();
    expect(api.startModelPerf).toHaveBeenCalledWith(
      jasmine.objectContaining({ image: "sdk:test", model_version: "1" }),
      7,
      "model-a",
    );
    expect(component.canRun()).toBeFalse();
  }));

  it("restores another version's active job and blocks a second start", fakeAsync(() => {
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run("running", "2") }));
    mount();
    flushMicrotasks();
    expect(component.activeRun()?.model_version).toBe("2");
    expect(component.canRun()).toBeFalse();
    void component.runProfiler();
    expect(api.startModelPerf).not.toHaveBeenCalled();
  }));

  it("keeps Start disabled until Stop is confirmed", fakeAsync(() => {
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run() }));
    mount();
    flushMicrotasks();
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run("stopping") }));
    void component.stopProfiler();
    flushMicrotasks();
    expect(api.stopModelPerf).toHaveBeenCalledWith(7, "model-a", "run-a");
    expect(component.canRun()).toBeFalse();
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), latest_run: run("cancelled") }));
    tick(3000);
    expect(component.canRun()).toBeTrue();
  }));

  it("restores only the selected version's settings while another version is active", fakeAsync(() => {
    const otherVersion = {
      ...run("running", "2"),
      image: "sdk:other",
      batch_size: 8,
      input_data: '{"data":[{"OTHER":[2]}]}',
    };
    const selectedVersion = {
      ...run("succeeded", "1"),
      image: "sdk:selected",
      batch_size: 2,
      concurrency_range: "2:4",
      measurement_request_count: 100,
      input_data: '{"data":[{"SELECTED":[1]}]}',
    };
    api.getModelPerfStatus.and.returnValue(
      of({
        ...idle(),
        active_run: otherVersion,
        latest_run: selectedVersion,
      }),
    );
    mount();
    flushMicrotasks();
    expect(component.activeRun()?.model_version).toBe("2");
    expect(component.hasActiveRun()).toBeTrue();
    expect(component.canRun()).toBeFalse();
    expect(component.image).toBe("sdk:selected");
    expect(component.batchSize).toBe(2);
    expect(component.concurrencyRange).toBe("2:4");
    expect(component.measurementRequestCount).toBe(100);
    expect(component.inputData).toBe(selectedVersion.input_data);
  }));

  it("keeps defaults when only another version has a saved run", fakeAsync(() => {
    api.getModelPerfStatus.and.returnValue(
      of({
        ...idle(),
        active_run: { ...run("running", "2"), image: "sdk:other", batch_size: 8 },
      }),
    );
    mount();
    flushMicrotasks();
    expect(component.image).toBe("sdk:test");
    expect(component.batchSize).toBe(1);
    expect(component.inputData).toBe("");
    expect(component.canRun()).toBeFalse();
  }));

  it("does not block another model and ignores the old model's late response", fakeAsync(() => {
    const pending = new Subject<ModelPerfStatusResponse>();
    api.getModelPerfStatus.and.returnValue(pending);
    mount();
    flushMicrotasks();
    api.getModelPerfStatus.and.returnValue(of(idle()));
    params.next(routeParams("model-b"));
    flushMicrotasks();
    pending.next({ ...idle(), active_run: run() });
    flushMicrotasks();
    expect(component.modelName()).toBe("model-b");
    expect(component.activeRun()).toBeNull();
    expect(component.canRun()).toBeTrue();
  }));

  it("shows status uncertainty and retries without enabling Start", fakeAsync(() => {
    api.getModelPerfStatus.and.returnValue(throwError(() => new Error("offline")));
    mount();
    flushMicrotasks();
    expect(component.canRun()).toBeFalse();
    expect(component.statusError()).toBeTruthy();
    api.getModelPerfStatus.and.returnValue(of(idle()));
    tick(5000);
    expect(component.canRun()).toBeTrue();
  }));

  it("refreshes server status after a conflicting start", fakeAsync(() => {
    mount();
    flushMicrotasks();
    api.startModelPerf.and.returnValue(
      throwError(() => ({ status: 409, error: { detail: "Active run: run-a" } })),
    );
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run() }));
    void component.runProfiler();
    flushMicrotasks();
    expect(component.activeRun()?.id).toBe("run-a");
    expect(component.canRun()).toBeFalse();
  }));

  it("does not let an older idle poll overwrite an accepted start", fakeAsync(() => {
    mount();
    flushMicrotasks();
    const pending = new Subject<ModelPerfStatusResponse>();
    api.getModelPerfStatus.and.returnValue(pending);
    void component.loadStatus();
    api.getModelPerfStatus.and.returnValue(of({ ...idle(), active_run: run("creating") }));
    void component.runProfiler();
    flushMicrotasks();
    pending.next(idle());
    flushMicrotasks();
    expect(component.canRun()).toBeFalse();
  }));

  it("stops polling after leaving the tab", fakeAsync(() => {
    mount();
    flushMicrotasks();
    const calls = api.getModelPerfStatus.calls.count();
    fixture.destroy();
    tick(10000);
    expect(api.getModelPerfStatus.calls.count()).toBe(calls);
  }));
});
