/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { of, throwError } from "rxjs";

import { BASE_PATH, CodeServerDTO, CodeServersService } from "../../api/generated/index";
import { CodeServersPageComponent } from "./code-servers-page.component";

describe("CodeServersPageComponent", () => {
  const creatingWorkspace: CodeServerDTO = {
    id: 3,
    name: "workspace",
    namespace: "triton-control",
    statefulset_name: "code-1-workspace",
    service_name: "code-1-workspace-svc",
    image: "nvcr.io/nvidia/tritonserver:25.02-py3",
    url: "/api/code-servers/3/proxy/",
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

  beforeEach(async () => {
    codeServersApi = jasmine.createSpyObj<CodeServersService>("CodeServersService", [
      "listCodeServersApiCodeServersGet",
      "createCodeServerApiCodeServersPost",
      "getCodeServerApiCodeServersCodeServerIdGet",
      "deleteCodeServerApiCodeServersCodeServerIdDelete",
    ]);
    codeServersApi.listCodeServersApiCodeServersGet.and.returnValue(of([]) as any);
    codeServersApi.createCodeServerApiCodeServersPost.and.returnValue(of(creatingWorkspace) as any);
    codeServersApi.getCodeServerApiCodeServersCodeServerIdGet.and.returnValue(
      of(readyWorkspace) as any,
    );
    codeServersApi.deleteCodeServerApiCodeServersCodeServerIdDelete.and.returnValue(of({}) as any);

    await TestBed.configureTestingModule({
      imports: [CodeServersPageComponent],
      providers: [
        { provide: CodeServersService, useValue: codeServersApi },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(CodeServersPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
    expect(codeServersApi.listCodeServersApiCodeServersGet).toHaveBeenCalled();
  });

  it("Create_SelectedThemeProvided_SendsThemeAndStartsPolling", async () => {
    // Arrange
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;
    component.theme = "Monokai";
    component.cpu = "2";
    component.memory = "4Gi";
    component.dockerconfigjson = '{"auths":{}}';

    // Act
    await component.create();
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
      }),
    );
    expect(codeServersApi.getCodeServerApiCodeServersCodeServerIdGet).toHaveBeenCalledWith(3);
    expect(component.selectedWorkspaceId()).toBeNull();
    expect(component.workspaces()[0].status).toBe("ready");
    expect(component.embeddedWorkspaceUrl()).toBeNull();
  });

  it("Load_ReadyWorkspaceReturned_ListsWithoutAutoSelect", async () => {
    // Arrange
    codeServersApi.listCodeServersApiCodeServersGet.and.returnValue(of([readyWorkspace]) as any);
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.load();

    // Assert
    expect(component.workspaces().length).toBe(1);
    expect(component.selectedWorkspaceId()).toBeNull();
    expect(component.embeddedWorkspaceUrl()).toBeNull();
  });

  it("Open_ReadyWorkspace_SelectsAndEmbedsWorkspace", async () => {
    // Arrange
    codeServersApi.listCodeServersApiCodeServersGet.and.returnValue(of([readyWorkspace]) as any);
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;
    await component.load();

    // Act
    component.open(readyWorkspace);

    // Assert
    expect(component.selectedWorkspaceId()).toBe(3);
    expect(`${component.embeddedWorkspaceUrl()}`).toContain(
      "/api/code-servers/3/proxy/?_tc_reload=",
    );
  });

  it("Refresh_ApiFails_SetsErrorMessage", async () => {
    // Arrange
    codeServersApi.getCodeServerApiCodeServersCodeServerIdGet.and.returnValue(
      throwError(() => ({ error: { detail: "backend failed" } })) as any,
    );
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;

    // Act
    await component.refresh(creatingWorkspace);

    // Assert
    expect(component.message()).toBe("Failed to refresh code server.");
    expect(component.messageTone()).toBe("error");
  });

  it("Delete_SelectedWorkspaceDeleted_ClearsSelectionAndFrame", async () => {
    // Arrange
    const fixture = TestBed.createComponent(CodeServersPageComponent);
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
    const fixture = TestBed.createComponent(CodeServersPageComponent);
    const component = fixture.componentInstance;
    component.name = "";

    // Act
    await component.create();

    // Assert
    expect(codeServersApi.createCodeServerApiCodeServersPost).not.toHaveBeenCalled();
    expect(component.messageTone()).toBe("error");
  });
});
