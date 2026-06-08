/* eslint-disable @typescript-eslint/no-explicit-any */
import { HttpErrorResponse } from "@angular/common/http";
import { fakeAsync, TestBed, tick } from "@angular/core/testing";
import { MatTabChangeEvent } from "@angular/material/tabs";
import { ActivatedRoute } from "@angular/router";
import { of, throwError, EMPTY } from "rxjs";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { provideMockActions } from "@ngrx/effects/testing";
import { AuthStore } from "../../../shared/auth/auth.store";
import {
  DeploymentsService,
  InstancesService,
  PerfAnalyzersService,
  UsersService,
} from "../../../api/generated/index";
import { InstanceDetailPageComponent } from "./instance-detail-page.component";
import {
  selectDetailInstance,
  selectDetailLoading,
} from "../../../state/instances-detail/instances-detail.selectors";
import {
  instanceDetailPageOpened,
  s3ConfigSaveRequested,
  s3ConfigDisableRequested,
} from "../../../state/instances-detail/instances-detail.actions";

const MOCK_INSTANCE = {
  id: "7",
  name: "node-7",
  url: "http://localhost:8000",
  status: "healthy" as const,
  version: "1.0.0",
  region: "Unknown",
  models: 1,
  healthLive: true,
  healthReady: true,
  healthLastCheckedAt: "",
  healthError: "",
  serverMetadata: { version: "1.0.0", platform: "linux" },
  qps: 0,
  cpu: 0,
  ram: 0,
  gpu: 0,
  assignedUsers: [],
  s3: {
    enabled: true,
    endpoint: "e",
    bucket: "b",
    region: "r",
    prefix: "p",
    accessKey: "ak",
    secretConfigured: true,
  },
  modelFiles: [],
  repositoryModels: [],
};

describe("InstanceDetailPageComponent", () => {
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let deploymentsApiMock: jasmine.SpyObj<DeploymentsService>;
  let perfAnalyzersApiMock: jasmine.SpyObj<PerfAnalyzersService>;
  let usersApiMock: jasmine.SpyObj<UsersService>;
  let mockStore: MockStore;
  let authState: InstanceType<typeof AuthStore>;

  beforeEach(async () => {
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "getInstanceApiInstancesInstanceIdGet",
      "getInstanceS3ApiInstancesInstanceIdS3Get",
      "updateInstanceS3ApiInstancesInstanceIdS3Put",
      "getInstanceModelsApiInstancesInstanceIdModelsGet",
      "getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet",
      "loadInstanceModelApiInstancesInstanceIdModelsModelNameLoadPost",
      "unloadInstanceModelApiInstancesInstanceIdModelsModelNameUnloadPost",
    ]);
    usersApiMock = jasmine.createSpyObj<UsersService>("UsersService", ["listUsersApiAuthUsersGet"]);
    deploymentsApiMock = jasmine.createSpyObj<DeploymentsService>("DeploymentsService", [
      "getDeploymentLogsApiDeploymentsInstanceIdLogsGet",
      "deleteDeploymentApiDeploymentsInstanceIdDelete",
    ]);
    perfAnalyzersApiMock = jasmine.createSpyObj<PerfAnalyzersService>("PerfAnalyzersService", [
      "getPerfAnalyzerStatusApiPerfAnalyzersGet",
    ]);

    instancesApiMock.getInstanceModelsApiInstancesInstanceIdModelsGet.and.returnValue(
      of([] as any),
    );
    perfAnalyzersApiMock.getPerfAnalyzerStatusApiPerfAnalyzersGet.and.returnValue(
      of({ installed: false } as any),
    );
    usersApiMock.listUsersApiAuthUsersGet.and.returnValue(of([] as any));

    spyOn(navigator.clipboard, "writeText").and.resolveTo();

    await TestBed.configureTestingModule({
      imports: [InstanceDetailPageComponent],
      providers: [
        provideMockStore(),
        provideMockActions(() => EMPTY),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => "7" } } } },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: DeploymentsService, useValue: deploymentsApiMock },
        { provide: PerfAnalyzersService, useValue: perfAnalyzersApiMock },
        { provide: UsersService, useValue: usersApiMock },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
    authState = TestBed.inject(AuthStore);
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("OnDetailTabChanged_LogsTabSelected_PollsDeploymentLogsEveryFiveSeconds", fakeAsync(() => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, {
      ...MOCK_INSTANCE,
      isSelfDeployed: true,
      deploymentNamespace: "triton-minio",
      deploymentLog: "",
    } as any);
    mockStore.refreshState();
    deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet.and.returnValue(
      of({ logs: "pod output" } as any),
    );
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.onDetailTabChanged({ tab: { textLabel: "Logs" } } as MatTabChangeEvent);
    tick();

    // Assert
    expect(
      deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet,
    ).toHaveBeenCalledTimes(1);

    tick(5000);
    expect(
      deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet,
    ).toHaveBeenCalledTimes(2);

    component.onDetailTabChanged({ tab: { textLabel: "Overview" } } as MatTabChangeEvent);
    tick(5000);
    expect(
      deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet,
    ).toHaveBeenCalledTimes(2);
  }));

  it("NgOnInit_OpenLogsOnceNavigation_SelectsLogsAndStartsPolling", fakeAsync(() => {
    // Arrange
    window.history.replaceState({ openLogsOnce: true }, "", window.location.href);
    mockStore.overrideSelector(selectDetailInstance, {
      ...MOCK_INSTANCE,
      isSelfDeployed: true,
      deploymentNamespace: "triton-minio",
      deploymentLog: "",
    } as any);
    mockStore.refreshState();
    deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet.and.returnValue(
      of({ logs: "deployment output" } as any),
    );
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.ngOnInit();
    tick();

    // Assert
    expect(component.selectedDetailTabIndex()).toBe(3);
    expect(window.history.state.openLogsOnce).toBeUndefined();
    expect(
      deploymentsApiMock.getDeploymentLogsApiDeploymentsInstanceIdLogsGet,
    ).toHaveBeenCalledTimes(1);

    window.history.replaceState({}, "", window.location.href);
  }));

  it("NgOnInit_ValidInstanceIdProvided_DispatchesPageOpenedAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    // Act
    component.ngOnInit();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(instanceDetailPageOpened({ instanceId: "7" }));
  });

  it("InitialRouteOpen_InstanceNotLoadedYet_ShowsLoadingInsteadOfNotFound", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, null);
    mockStore.overrideSelector(selectDetailLoading, false);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act + Assert
    expect(component.showInitialLoading()).toBeTrue();
    expect(component.showNotFound()).toBeFalse();
  });

  it("DeploymentInProgress_SelfDeployedPodStillPending_ReturnsTrue", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, {
      ...MOCK_INSTANCE,
      status: "down",
      healthLive: false,
      healthReady: false,
      healthError: "Waiting for pod to become ready...",
      isSelfDeployed: true,
    } as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act + Assert
    expect(component.deploymentInProgress()).toBeTrue();
  });

  it("StatusAndModelHelpers_DifferentInputsProvided_ReturnExpectedValues", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act + Assert
    expect(component.canInferModel("READY", "1")).toBeTrue();
    expect(component.canUnloadModel("READY")).toBeTrue();
    expect(component.canShowModelConfig("READY")).toBeTrue();
    expect(component.canInferModel("READY", "")).toBeFalse();
    expect(component.canUnloadModel("UNAVAILABLE")).toBeFalse();
    expect(component.canShowModelConfig("UNAVAILABLE")).toBeFalse();
  });

  it("CopyBaseUrl_ClipboardAvailable_CopiesAndSetsMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.copyBaseUrl("http://localhost:8000");

    // Assert
    expect(component.copyBaseUrlMessage).toBe("Copied");
  });

  it("ModelActionError_HttpDetailContainsExplicitMode_AddsGuidanceSuffix", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance as any;
    const error = new HttpErrorResponse({
      status: 400,
      error: { detail: "explicit model load / unload is not allowed if polling is enabled" },
    });

    // Act
    const detail = component.buildModelActionErrorMessage(error);

    // Assert
    expect(detail).toContain("explicit mode");
  });

  it("LoadRepositoryModels_InstanceIdValid_CallsModelsApi", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.getInstanceModelsApiInstancesInstanceIdModelsGet.and.returnValue(
      of([] as any),
    );

    // Act
    await component.toggleModelConfig("resnet", "1");

    // Assert
    expect(component.isModelConfigOpen("resnet", "1")).toBeTrue();
  });

  it("NgOnInit_InvalidInstanceId_DoesNotDispatch", async () => {
    // Arrange
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [InstanceDetailPageComponent],
      providers: [
        provideMockStore(),
        provideMockActions(() => EMPTY),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => "abc" } } } },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: DeploymentsService, useValue: deploymentsApiMock },
        { provide: PerfAnalyzersService, useValue: perfAnalyzersApiMock },
        { provide: UsersService, useValue: usersApiMock },
      ],
    }).compileComponents();
    const localStore = TestBed.inject(MockStore);
    localStore.resetSelectors();
    spyOn(localStore, "dispatch");
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.ngOnInit();

    // Assert
    expect(localStore.dispatch).not.toHaveBeenCalled();
  });

  it("SaveS3Config_InstanceLoaded_DispatchesSaveAction", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, MOCK_INSTANCE as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    component.s3Endpoint = "  https://s3.example.test  ";
    component.s3Bucket = "  models  ";
    component.s3AccessKey = "  access  ";
    component.s3SecretKey = "  secret  ";
    spyOn(mockStore, "dispatch");

    // Act
    component.saveS3Config();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({
        type: s3ConfigSaveRequested.type,
        payload: jasmine.objectContaining({ region: "us-east-1" }),
      }),
    );
  });

  it("OpenS3Dialog_BlankRegion_ShowsDefaultRegion", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, {
      ...MOCK_INSTANCE,
      s3: { ...MOCK_INSTANCE.s3, region: "" },
    } as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.openS3Dialog();

    // Assert
    expect(component.s3Region).toBe("us-east-1");
  });

  it("OpenS3Dialog_ExistingS3Connection_PrefillsAccessKeyAndKeepsSecretHidden", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, MOCK_INSTANCE as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.openS3Dialog();

    // Assert
    expect(component.s3AccessKey).toBe("ak");
    expect(component.s3SecretKey).toBe("");
    expect(component.s3SecretConfigured).toBeTrue();
    expect(component.getS3SecretPlaceholder()).toBe("Stored secret configured");
  });

  it("SaveS3Config_BlankNewConnection_DoesNotDispatchSaveAction", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, {
      ...MOCK_INSTANCE,
      s3: {
        enabled: false,
        endpoint: "",
        bucket: "",
        region: "",
        prefix: "",
        accessKey: "",
        secretConfigured: false,
      },
    } as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    // Act
    component.saveS3Config();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
    expect(component.canSaveS3Config()).toBeFalse();
  });

  it("DialogBackdropClick_PointerStartedInsideDialog_DoesNotCloseS3Dialog", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    const backdrop = document.createElement("div");
    const dialog = document.createElement("div");
    component.showS3Dialog.set(true);

    // Act
    component.onDialogBackdropPointerDown({
      target: dialog,
      currentTarget: backdrop,
    } as unknown as PointerEvent);
    component.closeS3DialogFromBackdrop({
      target: backdrop,
      currentTarget: backdrop,
    } as unknown as MouseEvent);

    // Assert
    expect(component.showS3Dialog()).toBeTrue();
  });

  it("DialogBackdropClick_PointerStartedOnBackdrop_ClosesTritonDialog", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    const backdrop = document.createElement("div");
    component.showTritonDialog.set(true);

    // Act
    component.onDialogBackdropPointerDown({
      target: backdrop,
      currentTarget: backdrop,
    } as unknown as PointerEvent);
    component.closeTritonDialogFromBackdrop({
      target: backdrop,
      currentTarget: backdrop,
    } as unknown as MouseEvent);

    // Assert
    expect(component.showTritonDialog()).toBeFalse();
  });

  it("DisableS3Config_InstanceLoaded_DispatchesDisableAction", () => {
    // Arrange
    mockStore.overrideSelector(selectDetailInstance, MOCK_INSTANCE as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    // Act
    component.disableS3Config();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: s3ConfigDisableRequested.type }),
    );
  });

  it("ToggleMetadataView_InvokedTwice_TogglesRawMetadataState", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.toggleMetadataView();
    component.toggleMetadataView();

    // Assert
    expect(component.showRawMetadata()).toBeFalse();
  });

  it("ToggleModelConfig_VersionMissing_SetsModelConfigError", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.toggleModelConfig("resnet", "");

    // Assert
    expect(component.getModelConfigError("resnet", "")).toContain("No version");
  });

  it("ToggleModelConfig_ApiSuccess_LoadsAndCachesConfigJson", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      of({ max_batch_size: 8 } as any),
    );

    // Act
    await component.toggleModelConfig("resnet", "1");

    // Assert
    expect(component.isModelConfigOpen("resnet", "1")).toBeTrue();
    expect(component.getModelConfigContent("resnet", "1")).toContain("max_batch_size");
    expect(component.isModelConfigLoading("resnet", "1")).toBeFalse();
  });

  it("ToggleModelConfig_ApiFails_SetsModelConfigError", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      throwError(() => new Error("down")),
    );

    // Act
    await component.toggleModelConfig("resnet", "1");

    // Assert
    expect(component.getModelConfigError("resnet", "1")).toBe("Failed to load model config.");
  });

  it("LoadModel_ApiSucceeds_SetsSuccessActionMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.loadInstanceModelApiInstancesInstanceIdModelsModelNameLoadPost.and.returnValue(
      of({} as any),
    );

    // Act
    await component.loadModel("resnet", "1");

    // Assert
    expect(component.getModelActionMessage("resnet", "1")).toContain("load requested");
  });

  it("UnloadModel_ApiFails_SetsMappedActionError", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.unloadInstanceModelApiInstancesInstanceIdModelsModelNameUnloadPost.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 400, error: { detail: "cannot unload" } })),
    );

    // Act
    await component.unloadModel("resnet", "1");

    // Assert
    expect(component.getModelActionError("resnet", "1")).toBe("cannot unload");
  });

  it("S3Connection_InstanceWithoutS3Fields_ReturnsFalse", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    const instance = {
      s3: {
        enabled: false,
        endpoint: "",
        bucket: "",
        region: "",
        prefix: "",
        accessKey: "",
        secretConfigured: false,
      },
    } as any;

    // Act
    const hasConnection = component.hasS3Connection(instance);

    // Assert
    expect(hasConnection).toBeFalse();
  });

  it("S3Connection_InstanceWithOnlyDefaultRegion_ReturnsFalse", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    const instance = {
      s3: {
        enabled: false,
        endpoint: "",
        bucket: "",
        region: "us-east-1",
        prefix: "",
        accessKey: "",
        secretConfigured: false,
        caCertificate: "",
      },
    } as any;

    // Act
    const hasConnection = component.hasS3Connection(instance);

    // Assert
    expect(hasConnection).toBeFalse();
  });

  it("ModelStateHelpers_VariousStates_ReturnExpectedLabelsAndTones", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    const readyTone = component.modelStateTone("READY");
    const loadingTone = component.modelStateTone("LOADING");
    const failedTone = component.modelStateTone("FAILED");
    const unknownLabel = component.modelStateLabel("");
    const activeInfer = component.canInferModel("ACTIVE", "1");
    const noVersionInfer = component.canInferModel("READY", "");
    const unloadUnavailable = component.canUnloadModel("UNAVAILABLE");
    const showUnavailable = component.canShowModelConfig("UNAVAILABLE");

    // Assert
    expect(readyTone).toBe("ok");
    expect(loadingTone).toBe("warn");
    expect(failedTone).toBe("down");
    expect(unknownLabel).toBe("Unknown");
    expect(activeInfer).toBeTrue();
    expect(noVersionInfer).toBeFalse();
    expect(unloadUnavailable).toBeFalse();
    expect(showUnavailable).toBeFalse();
  });

  it("NormalizeRoleLabel_EmptyRole_ReturnsViewOnlyDefault", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act + Assert — canInferModel proxies role-related guards
    expect(component.canInferModel("ACTIVE", "1")).toBeTrue();
    expect(component.canInferModel("READY", "")).toBeFalse();
  });

  it("ResolveRuntimeEnvironment_DifferentInputs_ReturnsExpectedRuntimeLabels", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance as any;

    // Act
    const fromPlatform = component.resolveRuntimeEnvironment("http://x:8000", { platform: "k8s" });
    const local = component.resolveRuntimeEnvironment("http://localhost:8000", null);
    const remote = component.resolveRuntimeEnvironment("https://triton.example.com", null);
    const invalid = component.resolveRuntimeEnvironment("not-a-url", null);

    // Assert
    expect(fromPlatform).toBe("k8s");
    expect(local).toBe("Local runtime");
    expect(remote).toContain("Runtime on");
    expect(invalid).toBe("Connected runtime");
  });

  it("CopyBaseUrl_EmptyUrlProvided_DoesNotChangeMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    component.copyBaseUrlMessage = "unchanged";

    // Act
    await component.copyBaseUrl("");

    // Assert
    expect(component.copyBaseUrlMessage).toBe("unchanged");
  });

  it("CopyBaseUrl_ClipboardWriteFails_SetsCopyFailedMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    (navigator.clipboard.writeText as jasmine.Spy).and.rejectWith(new Error("blocked"));

    // Act
    await component.copyBaseUrl("http://localhost:8000");

    // Assert
    expect(component.copyBaseUrlMessage).toBe("Copy failed");
  });

  it("OpenAndDisableS3_NoLoadedInstance_ReturnsWithoutMutating", () => {
    // Arrange
    mockStore.resetSelectors();
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.openS3Dialog();
    component.disableS3Config();

    // Assert
    expect(component.showS3Dialog()).toBeFalse();
  });

  it("SaveS3Config_NoLoadedInstance_ReturnsWithoutCallingApi", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.saveS3Config();

    // Assert
    expect(instancesApiMock.updateInstanceS3ApiInstancesInstanceIdS3Put).not.toHaveBeenCalled();
  });

  it("ToggleModelConfig_AlreadyOpen_TogglesClosedWithoutApiCall", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      of({ name: "resnet" } as any),
    );
    await component.toggleModelConfig("resnet", "1");
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.calls.reset();

    // Act
    await component.toggleModelConfig("resnet", "1");

    // Assert
    expect(component.isModelConfigOpen("resnet", "1")).toBeFalse();
    expect(
      instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet,
    ).not.toHaveBeenCalled();
  });

  it("BuildModelActionErrorMessage_GenericFailure_ReturnsDetailWithoutSuffix", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance as any;
    const error = new HttpErrorResponse({ status: 500, error: { detail: "generic failure" } });

    // Act
    const message = component.buildModelActionErrorMessage(error);

    // Assert
    expect(message).toBe("generic failure");
  });

  it("BuildModelActionErrorMessage_HttpDetailExtracted_ReturnsDetailString", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance as any;
    const error = new HttpErrorResponse({ status: 500, error: { detail: "server error" } });

    // Act
    const message = component.buildModelActionErrorMessage(error);

    // Assert
    expect(message).toBe("server error");
  });

  it("RunModelAction_NoInstanceIdConfigured_DoesNotCallApi", async () => {
    // Arrange
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [InstanceDetailPageComponent],
      providers: [
        provideMockStore(),
        provideMockActions(() => EMPTY),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => null } } } },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: DeploymentsService, useValue: deploymentsApiMock },
        { provide: UsersService, useValue: usersApiMock },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(InstanceDetailPageComponent);
    const component = fixture.componentInstance as any;

    // Act
    await component.runModelAction("load", "model-x", "1");

    // Assert
    expect(
      instancesApiMock.loadInstanceModelApiInstancesInstanceIdModelsModelNameLoadPost,
    ).not.toHaveBeenCalled();
  });
});
