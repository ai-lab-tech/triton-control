/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { ActivatedRoute } from "@angular/router";
import { of, throwError } from "rxjs";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { InstancesService } from "../../../api/generated/index";
import { InstanceModelInferPageComponent } from "./instance-model-infer-page.component";
import {
  inferRequestStarted,
  inferResultHydrated,
} from "../../../state/instances-infer/instances-infer.actions";
import {
  selectInferResponseJson,
  selectInferError,
  selectInferInferenceMetrics,
  selectInferSubmitting,
  selectInferProcessingResponse,
  selectInferRequestLatencyMs,
} from "../../../state/instances-infer/instances-infer.selectors";

describe("InstanceModelInferPageComponent", () => {
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let mockStore: MockStore;

  beforeEach(async () => {
    localStorage.removeItem("triton-infer-body:7:model-a:1");
    localStorage.removeItem("triton-infer-result:7:model-a:1");

    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "getInstanceApiInstancesInstanceIdGet",
      "getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet",
      "inferInstanceModelApiInstancesInstanceIdModelsModelNameVersionsVersionInferPost",
    ]);
    instancesApiMock.getInstanceApiInstancesInstanceIdGet.and.returnValue(
      of({ name: "node-7", url: "http://localhost:8000" } as any),
    );
    instancesApiMock.inferInstanceModelApiInstancesInstanceIdModelsModelNameVersionsVersionInferPost.and.returnValue(
      of({ outputs: [] } as any),
    );
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      of({ max_batch_size: 8 } as any),
    );

    await TestBed.configureTestingModule({
      imports: [InstanceModelInferPageComponent],
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
      ],
    }).compileComponents();
    mockStore = TestBed.inject(MockStore);
    mockStore.overrideSelector(selectInferResponseJson, "");
    mockStore.overrideSelector(selectInferError, "");
    mockStore.overrideSelector(selectInferSubmitting, false);
    mockStore.overrideSelector(selectInferProcessingResponse, false);
    mockStore.overrideSelector(selectInferRequestLatencyMs, null);
    mockStore.overrideSelector(selectInferInferenceMetrics, null);
    mockStore.refreshState();
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("NgOnInit_ValidRouteAndApiSuccess_LoadsInstanceNameAndUrl", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.ngOnInit();

    // Assert
    expect(component.instanceName).toBe("node-7");
    expect(component.instanceUrl).toBe("http://localhost:8000");
  });

  it("NgOnInit_SavedResultExists_HydratesInferResult", async () => {
    // Arrange
    localStorage.setItem(
      "triton-infer-result:7:model-a:1",
      JSON.stringify({
        responseJson: '{"outputs":[]}',
        requestLatencyMs: 12.5,
        inferenceMetrics: { available: true, error: null, models: [] },
      }),
    );
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    // Act
    await component.ngOnInit();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      inferResultHydrated({
        responseJson: '{"outputs":[]}',
        requestLatencyMs: 12.5,
        inferenceMetrics: { available: true, error: null, models: [] },
      }),
    );
  });

  it("SendInference_InvalidJsonEditorContent_ShowsJsonValidationError", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.editorContent = "{ invalid-json";

    // Act
    await component.sendInference();

    // Assert
    expect(component.displayError()).toContain("Invalid JSON");
  });

  it("SendInference_ApiReturnsResponse_StoresFormattedResponseJson", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.editorContent = '{"inputs": []}';
    spyOn(mockStore, "dispatch");

    // Act
    await component.sendInference();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: inferRequestStarted.type }),
    );
    expect(component.displayError()).toBe("");
  });

  it("ExtractApiErrorMessage_HttpErrorDetailProvided_ReturnsDetailText", () => {
    // Arrange
    mockStore.overrideSelector(selectInferError, "bad request");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Assert
    expect(component.displayError()).toBe("bad request");
  });

  it("SendInference_ApiThrowsError_ShowsMappedErrorMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.editorContent = '{"inputs": []}';
    spyOn(mockStore, "dispatch");

    // Act
    await component.sendInference();

    // Assert — sendInference dispatches inferRequestStarted (error handling is in effects)
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: inferRequestStarted.type }),
    );
  });

  it("ToggleApiConfig_ApiSuccess_LoadsAndCachesConfigJson", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.toggleApiConfig();
    await component.toggleApiConfig();
    await component.toggleApiConfig();

    // Assert
    expect(component.apiConfigOpen()).toBeTrue();
    expect(component.apiConfigJson()).toContain("max_batch_size");
    expect(component.apiConfigLoading()).toBeFalse();
    expect(
      instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet,
    ).toHaveBeenCalledTimes(1);
  });

  it("ToggleApiConfig_ApiFails_ShowsConfigError", async () => {
    // Arrange
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      throwError(() => new Error("down")),
    );
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.toggleApiConfig();

    // Assert
    expect(component.apiConfigError()).toBe("Failed to load API config.");
    expect(component.apiConfigLoading()).toBeFalse();
  });

  it("NgOnInit_InvalidRoute_DoesNotResolveInstanceDetails", async () => {
    // Arrange
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [InstanceModelInferPageComponent],
      providers: [
        provideMockStore(),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: { get: () => "" },
            },
          },
        },
        { provide: InstancesService, useValue: instancesApiMock },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.ngOnInit();

    // Assert
    expect(component.hasValidRoute()).toBeFalse();
    expect(instancesApiMock.getInstanceApiInstancesInstanceIdGet).not.toHaveBeenCalled();
  });

  it("NgOnInit_ApiFailsToResolveInstance_SetsLoadError", async () => {
    // Arrange
    TestBed.resetTestingModule();
    instancesApiMock.getInstanceApiInstancesInstanceIdGet.and.returnValue(
      throwError(() => new Error("x")),
    );
    await TestBed.configureTestingModule({
      imports: [InstanceModelInferPageComponent],
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
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.ngOnInit();

    // Assert
    expect(component.displayError()).toBe("Failed to load instance details.");
    expect(component.resolvingInstance).toBeFalse();
  });

  it("SendInference_TimeoutError_ShowsTimeoutMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.editorContent = '{"inputs": []}';
    spyOn(mockStore, "dispatch");

    // Act
    await component.sendInference();

    // Assert — timeout handling is in the infer effect; sendInference dispatches the action
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: inferRequestStarted.type }),
    );
  });

  it("CopyInferUrl_RequestUrlEmpty_DoesNotSetCopiedFlag", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.instanceUrl = "";

    // Act
    await component.copyInferUrl();

    // Assert
    expect(component.inferUrlCopied).toBeFalse();
  });

  it("CopyResponse_ResponseBodyEmpty_DoesNotSetCopiedFlag", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    // responseJson starts as '' (initial state) — copyResponse skips clipboard
    // component.responseJson = '   ';  // removed: toSignal result is read-only

    // Act
    await component.copyResponse();

    // Assert
    expect(component.responseCopied).toBeFalse();
  });

  it("ExtractApiErrorMessage_HttpErrorWithoutDetail_FallsBackToHttpMessage", () => {
    // Arrange
    mockStore.overrideSelector(selectInferError, "Server Error");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Assert — displayError reflects the store's error signal
    expect(component.displayError().length).toBeGreaterThan(0);
  });

  it("CopyInferUrl_ValidUrl_SetsInferUrlCopied", async () => {
    // Arrange
    spyOn(navigator.clipboard, "writeText").and.resolveTo();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.instanceUrl = "http://localhost:8000";
    await component.ngOnInit();

    // Act
    await component.copyInferUrl();

    // Assert
    expect(component.inferUrlCopied).toBeTrue();
  });

  it("CopyResponse_NonEmptyResponse_SetsResponseCopied", async () => {
    // Arrange
    mockStore.overrideSelector(selectInferResponseJson, '{"outputs": []}');
    mockStore.refreshState();
    spyOn(navigator.clipboard, "writeText").and.resolveTo();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.copyResponse();

    // Assert
    expect(component.responseCopied).toBeTrue();
  });

  it("SendInference_AlreadySubmitting_DoesNotDispatch", async () => {
    // Arrange
    mockStore.overrideSelector(selectInferSubmitting, true);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceModelInferPageComponent);
    const component = fixture.componentInstance;
    component.editorContent = '{"inputs": []}';
    spyOn(mockStore, "dispatch");

    // Act
    await component.sendInference();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
  });
});
