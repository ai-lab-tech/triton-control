/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { provideMockActions } from "@ngrx/effects/testing";
import { Observable, of, throwError } from "rxjs";

import { PerfAnalyzersService } from "../../api/generated/index";
import {
  profileLastResultLoadStarted,
  profileLastResultLoadSucceeded,
  profileRunFailed,
  profileRunStarted,
  profileRunSucceeded,
} from "./instances-profile.actions";
import { InstancesProfileEffects } from "./instances-profile.effects";

describe("InstancesProfileEffects", () => {
  let actions$: Observable<any>;
  let effects: InstancesProfileEffects;
  let perfAnalyzersApiMock: jasmine.SpyObj<PerfAnalyzersService>;

  beforeEach(() => {
    perfAnalyzersApiMock = jasmine.createSpyObj<PerfAnalyzersService>("PerfAnalyzersService", [
      "getLatestPerfAnalyzerRunApiPerfAnalyzersRunsLatestGet",
      "runPerfAnalyzerApiPerfAnalyzersRunsPost",
    ]);

    TestBed.configureTestingModule({
      providers: [
        InstancesProfileEffects,
        provideMockActions(() => actions$),
        { provide: PerfAnalyzersService, useValue: perfAnalyzersApiMock },
      ],
    });

    effects = TestBed.inject(InstancesProfileEffects);
  });

  it("LoadLastProfileResult_ApiReturnsResult_DispatchesSuccess", (done) => {
    // Arrange
    perfAnalyzersApiMock.getLatestPerfAnalyzerRunApiPerfAnalyzersRunsLatestGet.and.returnValue(
      of({
        found: true,
        batch_size: 2,
        concurrency_range: "1:4:1",
        measurement_request_count: 80,
        input_data: "{}",
        command: ["perf_analyzer"],
        output: "previous output",
      } as any),
    );
    actions$ = of(
      profileLastResultLoadStarted({
        key: "7:resnet:1",
        instanceId: "7",
        modelName: "resnet",
        version: "1",
      }),
    );

    // Act / Assert
    effects.loadLastProfileResult$.subscribe((action) => {
      expect(
        perfAnalyzersApiMock.getLatestPerfAnalyzerRunApiPerfAnalyzersRunsLatestGet,
      ).toHaveBeenCalledWith(7, "resnet", "1");
      expect(action).toEqual(
        profileLastResultLoadSucceeded({
          key: "7:resnet:1",
          batchSize: 2,
          concurrencyRange: "1:4:1",
          measurementRequestCount: 80,
          inputData: "{}",
          command: ["perf_analyzer"],
          output: "previous output",
        }),
      );
      done();
    });
  });

  it("RunProfile_ApiSucceeds_DispatchesSuccess", (done) => {
    // Arrange
    perfAnalyzersApiMock.runPerfAnalyzerApiPerfAnalyzersRunsPost.and.returnValue(
      of({ command: ["perf_analyzer"], output: "done" } as any),
    );
    actions$ = of(
      profileRunStarted({
        key: "7:resnet:1",
        instanceId: "7",
        modelName: "resnet",
        version: "1",
        batchSize: 4,
        concurrencyRange: "1:8:1",
        measurementRequestCount: 120,
        inputData: "{}",
      }),
    );

    // Act / Assert
    effects.runProfile$.subscribe((action) => {
      expect(perfAnalyzersApiMock.runPerfAnalyzerApiPerfAnalyzersRunsPost).toHaveBeenCalledWith({
        instance_id: 7,
        model_name: "resnet",
        model_version: "1",
        batch_size: 4,
        concurrency_range: "1:8:1",
        measurement_request_count: 120,
        input_data: "{}",
      });
      expect(action).toEqual(
        profileRunSucceeded({ key: "7:resnet:1", command: ["perf_analyzer"], output: "done" }),
      );
      done();
    });
  });

  it("RunProfile_ApiFails_DispatchesFailure", (done) => {
    // Arrange
    perfAnalyzersApiMock.runPerfAnalyzerApiPerfAnalyzersRunsPost.and.returnValue(
      throwError(() => new Error("down")),
    );
    actions$ = of(
      profileRunStarted({
        key: "7:resnet:1",
        instanceId: "7",
        modelName: "resnet",
        version: "1",
        batchSize: 1,
        concurrencyRange: "1",
        measurementRequestCount: 50,
      }),
    );

    // Act / Assert
    effects.runProfile$.subscribe((action) => {
      expect(action).toEqual(
        profileRunFailed({ key: "7:resnet:1", message: "Profiler run failed." }),
      );
      done();
    });
  });
});
