/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { Router } from "@angular/router";
import { of, throwError } from "rxjs";

import { BASE_PATH, CodeServerDTO, CodeServersService } from "../../api/generated/index";
import { AuthService } from "../../shared/auth/auth.service";
import { DevelopmentPageComponent } from "./development-page.component";

describe("DevelopmentPageComponent", () => {
  const creatingWorkspace: CodeServerDTO = {
    id: 3,
    name: "workspace",
    namespace: "triton-control",
    statefulset_name: "code-1-workspace",
    service_name: "code-1-workspace-svc",
    image: "nvcr.io/nvidia/tritonserver:25.02-py3",
    url: "/api/development/3/proxy/",
    status: "creating",
    status_message: "Waiting for pod readiness.",
    applied_resources: [],
  };
  const readyWorkspace: CodeServerDTO = {
    ...creatingWorkspace,
    status: "ready",
    status_message: "code-1-workspace-0: Running (Ready)",
  };

  let codeServersApi: jasmine.SpyObj<CodeServersService>;
  let authService: jasmine.SpyObj<AuthService>;
  let router: jasmine.SpyObj<Router>;

  beforeEach(async () => {
    window.sessionStorage.clear();
    codeServersApi = jasmine.createSpyObj<CodeServersService>("CodeServersService", [
      "listCodeServersApiCodeServersGet",
      "createCodeServerApiCodeServersPost",
      "getCodeServerApiCodeServersCodeServerIdGet",
      "deleteCodeServerApiCodeServersCodeServerIdDelete",
      "consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet",
    ]);
    codeServersApi.listCodeServersApiCodeServersGet.and.returnValue(of([]) as any);
    codeServersApi.createCodeServerApiCodeServersPost.and.returnValue(of(creatingWorkspace) as any);
    codeServersApi.getCodeServerApiCodeServersCodeServerIdGet.and.returnValue(
      of(readyWorkspace) as any,
    );
    codeServersApi.deleteCodeServerApiCodeServersCodeServerIdDelete.and.returnValue(of({}) as any);
    codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet.and.returnValue(
      of({ instance_id: null }) as any,
    );
    authService = jasmine.createSpyObj<AuthService>("AuthService", [
      "refreshSession",
      "getAccessToken",
    ]);
    authService.refreshSession.and.resolveTo();
    authService.getAccessToken.and.returnValue("token-1");
    router = jasmine.createSpyObj<Router>("Router", ["navigateByUrl"]);
    Object.defineProperty(router, "url", { value: "/development", configurable: true });

    await TestBed.configureTestingModule({
      imports: [DevelopmentPageComponent],
      providers: [
        { provide: CodeServersService, useValue: codeServersApi },
        { provide: AuthService, useValue: authService },
        { provide: BASE_PATH, useValue: "" },
        { provide: Router, useValue: router },
      ],
    }).compileComponents();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
    expect(codeServersApi.listCodeServersApiCodeServersGet).toHaveBeenCalled();
  });

  it("Create_SelectedThemeProvided_SendsThemeAndStartsPolling", async () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.theme = "Monokai";
    component.cpu = "2";
    component.memory = "4Gi";
    component.dockerconfigjson = '{"auths":{}}';

    // Act
    await component.create();
    await Promise.resolve();
    await Promise.resolve();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        name: "workspace",
        theme: "Monokai",
        cpu: "2",
        cpu_limit: "2",
        memory: "4Gi",
        memory_limit: "4Gi",
        dockerconfigjson: '{"auths":{}}',
        image_has_code_server: false,
      }),
    );
    expect(codeServersApi.getCodeServerApiCodeServersCodeServerIdGet).toHaveBeenCalledWith(3);
    expect(component.selectedWorkspaceId()).toBe(3);
    expect(component.workspaces()[0].status).toBe("ready");
    expect(component.embeddedWorkspaceUrl()).not.toBeNull();
  });

  it("Load_ReadyWorkspaceReturned_SelectsAndEmbedsWorkspace", async () => {
    // Arrange
    codeServersApi.listCodeServersApiCodeServersGet.and.returnValue(of([readyWorkspace]) as any);
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.load();

    // Assert
    expect(component.workspaces().length).toBe(1);
    expect(component.selectedWorkspaceId()).toBe(3);
    expect(`${component.embeddedWorkspaceUrl()}`).toContain(
      "/api/development/3/proxy/?_tc_reload=",
    );
  });

  it("Refresh_ApiFails_SetsErrorMessage", async () => {
    // Arrange
    codeServersApi.getCodeServerApiCodeServersCodeServerIdGet.and.returnValue(
      throwError(() => ({ error: { detail: "backend failed" } })) as any,
    );
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.refresh(creatingWorkspace);

    // Assert
    expect(component.message()).toBe("Failed to refresh Development workspace.");
    expect(component.messageTone()).toBe("error");
  });

  it("Delete_SelectedWorkspaceDeleted_ClearsSelectionAndFrame", async () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.workspaces.set([readyWorkspace]);
    component.selectWorkspace(readyWorkspace);

    // Act
    await component.delete(readyWorkspace);

    // Assert
    expect(codeServersApi.deleteCodeServerApiCodeServersCodeServerIdDelete).toHaveBeenCalledWith(3);
    expect(component.workspaces()).toEqual([]);
    expect(component.selectedWorkspaceId()).toBeNull();
    expect(component.embeddedWorkspaceUrl()).toBeNull();
  });

  it("Create_InvalidForm_DoesNotCallApi", async () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.name = "";

    // Act
    await component.create();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).not.toHaveBeenCalled();
    expect(component.messageTone()).toBe("error");
  });

  it("Create_ImageHasCodeServerEnabled_SendsTrueFlag", async () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.imageHasCodeServer = true;

    // Act
    await component.create();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        image_has_code_server: true,
      }),
    );
  });

  it("Create_RequestCanceled_ShowsCanceledMessage", async () => {
    // Arrange
    codeServersApi.createCodeServerApiCodeServersPost.and.returnValue(
      throwError(() => new Error("Canceled")) as any,
    );
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.create();

    // Assert
    expect(component.messageTone()).toBe("error");
    expect(component.message()).toContain("Request was canceled in the browser");
  });

  it("Create_RefreshSessionFails_StillCallsCreateApi", async () => {
    // Arrange
    authService.refreshSession.and.rejectWith(new Error("Not authenticated"));
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.create();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).toHaveBeenCalled();
  });

  it("Create_GpuCountAsNumber_SendsParsedGpuCount", async () => {
    // Arrange
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.gpuCount = 1;

    // Act
    await component.create();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        gpu_count: 1,
      }),
    );
  });

  it("CodeServerMessage_DeploymentCreated_NavigatesToInstanceLogs", async () => {
    // Arrange
    TestBed.createComponent(DevelopmentPageComponent);

    // Act
    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          source: "triton-control-deploy",
          type: "deploymentCreated",
          instanceId: 42,
        },
        origin: window.location.origin,
      }),
    );
    await Promise.resolve();
    await Promise.resolve();

    // Assert
    expect(
      codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet,
    ).toHaveBeenCalled();
    expect(router.navigateByUrl).toHaveBeenCalledWith("/instances/42", {
      state: { openLogsOnce: true },
    });
  });

  it("CodeServerMessage_NullOriginDeploymentCreated_NavigatesToInstanceLogs", async () => {
    // Arrange
    TestBed.createComponent(DevelopmentPageComponent);

    // Act
    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          source: "triton-control-deploy",
          type: "deploymentCreated",
          instanceId: 43,
        },
        origin: "null",
      }),
    );
    await Promise.resolve();
    await Promise.resolve();

    // Assert
    expect(router.navigateByUrl).toHaveBeenCalledWith("/instances/43", {
      state: { openLogsOnce: true },
    });
  });

  it("DeploymentNavigationPoll_TargetReturned_NavigatesToInstanceLogs", async () => {
    // Arrange
    codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet.and.returnValue(
      of({ instance_id: 46 }) as any,
    );
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;

    // Act
    await (component as any).pollDeploymentNavigationTarget();

    // Assert
    expect(router.navigateByUrl).toHaveBeenCalledWith("/instances/46", {
      state: { openLogsOnce: true },
    });
  });

  it("CodeServerMessage_ReplayedDeploymentCreated_DoesNotNavigateAgain", async () => {
    // Arrange
    TestBed.createComponent(DevelopmentPageComponent);
    const message = new MessageEvent("message", {
      data: {
        source: "triton-control-deploy",
        type: "deploymentCreated",
        instanceId: 45,
      },
      origin: window.location.origin,
    });

    // Act
    window.dispatchEvent(message);
    await Promise.resolve();
    await Promise.resolve();
    router.navigateByUrl.calls.reset();
    window.dispatchEvent(message);
    await Promise.resolve();
    await Promise.resolve();

    // Assert
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it("FrameLoaded_PendingDeploymentNavigationTarget_DoesNotNavigateBackToInstance", () => {
    // Arrange
    codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet.and.returnValue(
      of({ instance_id: 44 }) as any,
    );
    const fixture = TestBed.createComponent(DevelopmentPageComponent);
    const component = fixture.componentInstance;
    component.workspaces.set([readyWorkspace]);
    component.selectWorkspace(readyWorkspace);

    // Act
    component.onFrameLoaded();

    // Assert
    expect(
      codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet,
    ).not.toHaveBeenCalled();
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });
});
