/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { ActivatedRoute } from "@angular/router";
import { of } from "rxjs";
import { MockStore, provideMockStore } from "@ngrx/store/testing";

import { InstancesService, PerfAnalyzersService } from "../../../api/generated/index";
import {
  profileLastResultLoadStarted,
  profilePageOpened,
  profileRunStarted,
} from "../../../state/instances-profile/instances-profile.actions";
import {
  selectActiveProfileEntry,
  selectActiveKey,
  selectProfileError,
  selectProfileOutput,
  selectProfileRunning,
} from "../../../state/instances-profile/instances-profile.selectors";
import { InstanceModelProfilePageComponent } from "./instance-model-profile-page.component";

describe("InstanceModelProfilePageComponent", () => {
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let perfAnalyzersApiMock: jasmine.SpyObj<PerfAnalyzersService>;
  let mockStore: MockStore;

  beforeEach(async () => {
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "getInstanceApiInstancesInstanceIdGet",
    ]);
    perfAnalyzersApiMock = jasmine.createSpyObj<PerfAnalyzersService>("PerfAnalyzersService", [
      "getLatestPerfAnalyzerRunApiPerfAnalyzersRunsLatestGet",
      "getPerfAnalyzerStatusApiPerfAnalyzersGet",
    ]);

    instancesApiMock.getInstanceApiInstancesInstanceIdGet.and.returnValue(
      of({ name: "node-7", url: "http://localhost:8000" } as any),
    );
    perfAnalyzersApiMock.getPerfAnalyzerStatusApiPerfAnalyzersGet.and.returnValue(
      of({ installed: true } as any),
    );

    await TestBed.configureTestingModule({
      imports: [InstanceModelProfilePageComponent],
      providers: [
        provideMockStore(),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: {
                get: (key: string) =>
                  key === "id"
                    ? "7"
                    : key === "modelName"
                      ? "model-a"
                      : key === "version"
                        ? "1"
                        : null,
              },
            },
          },
        },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: PerfAnalyzersService, useValue: perfAnalyzersApiMock },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
    mockStore.overrideSelector(selectActiveKey, "7:model-a:1");
    mockStore.overrideSelector(selectActiveProfileEntry, { error: "", output: "", command: [] });
    mockStore.overrideSelector(selectProfileRunning, false);
    mockStore.overrideSelector(selectProfileOutput, "");
    mockStore.overrideSelector(selectProfileError, "");
    mockStore.refreshState();
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("NgOnInit_ValidRouteAndApiSuccess_LoadsInstanceAndStatus", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelProfilePageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.ngOnInit();

    // Assert
    expect(component.instanceName).toBe("node-7");
    expect(component.installed()).toBeTrue();
  });

  it("RunProfiler_ValidForm_SendsSelectedModelRunPayload", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelProfilePageComponent);
    const component = fixture.componentInstance;
    await component.ngOnInit();
    component.batchSize = 4;
    component.concurrencyRange = " 1:8:1 ";
    component.measurementRequestCount = 120;
    spyOn(mockStore, "dispatch");

    // Act
    await component.runProfiler();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      profileRunStarted({
        key: "7:model-a:1",
        instanceId: "7",
        modelName: "model-a",
        version: "1",
        batchSize: 4,
        concurrencyRange: "1:8:1",
        measurementRequestCount: 120,
        inputData: component.inputData,
      }),
    );
  });

  it("NgOnInit_ValidRoute_LoadsLastResultFromBackendState", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelProfilePageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    // Act
    await component.ngOnInit();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(profilePageOpened({ key: "7:model-a:1" }));
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      profileLastResultLoadStarted({
        key: "7:model-a:1",
        instanceId: "7",
        modelName: "model-a",
        version: "1",
      }),
    );
  });

  it("ErrorSelector_ProfileErrorPresent_ExposesDisplayError", () => {
    // Arrange
    mockStore.overrideSelector(selectProfileError, "Profiler run failed.");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceModelProfilePageComponent);
    const component = fixture.componentInstance;

    // Assert
    expect(component.error()).toBe("Profiler run failed.");
  });
});
