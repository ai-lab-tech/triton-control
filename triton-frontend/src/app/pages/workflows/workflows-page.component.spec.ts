import { TestBed } from "@angular/core/testing";
import { of } from "rxjs";

import { BASE_PATH, WorkflowsService } from "../../api/generated/index";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";
import { WorkflowsPageComponent } from "./workflows-page.component";

describe("WorkflowsPageComponent", () => {
  let workflowsApi: jasmine.SpyObj<WorkflowsService>;
  let auth: jasmine.SpyObj<AuthService>;
  let chrome: jasmine.SpyObj<ChromeService>;

  beforeEach(async () => {
    workflowsApi = jasmine.createSpyObj<WorkflowsService>("WorkflowsService", [
      "getArgoWorkflowsStatusApiWorkflowsGet",
    ]);
    auth = jasmine.createSpyObj<AuthService>("AuthService", ["refreshSession"]);
    chrome = jasmine.createSpyObj<ChromeService>("ChromeService", ["hideTopbar", "showTopbar"]);
    auth.refreshSession.and.resolveTo();
    workflowsApi.getArgoWorkflowsStatusApiWorkflowsGet.and.returnValue(
      of({
        enabled: true,
        ready: true,
        status: "ready",
        status_message: "ok",
        namespace: "triton-control",
        service_name: "argo-server",
        base_path: "/api/workflows/proxy/",
      }) as unknown as ReturnType<WorkflowsService["getArgoWorkflowsStatusApiWorkflowsGet"]>,
    );

    await TestBed.configureTestingModule({
      imports: [WorkflowsPageComponent],
      providers: [
        { provide: WorkflowsService, useValue: workflowsApi },
        { provide: AuthService, useValue: auth },
        { provide: ChromeService, useValue: chrome },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();
  });

  it("loads status and embeds the ready Argo server", async () => {
    const fixture = TestBed.createComponent(WorkflowsPageComponent);
    const component = fixture.componentInstance;

    await component.load();

    expect(workflowsApi.getArgoWorkflowsStatusApiWorkflowsGet).toHaveBeenCalled();
    expect(component.frameUrl()).not.toBeNull();
    expect(`${component.frameUrl()}`).toContain("/api/workflows/proxy/?_tc_reload=");
  });

  it("shows the disabled state without an iframe", async () => {
    workflowsApi.getArgoWorkflowsStatusApiWorkflowsGet.and.returnValue(
      of({
        enabled: false,
        ready: false,
        status: "disabled",
        status_message: "disabled",
        namespace: "triton-control",
        service_name: "argo-server",
        base_path: "/api/workflows/proxy/",
      }) as unknown as ReturnType<WorkflowsService["getArgoWorkflowsStatusApiWorkflowsGet"]>,
    );
    const fixture = TestBed.createComponent(WorkflowsPageComponent);
    const component = fixture.componentInstance;

    await component.load();

    expect(component.frameUrl()).toBeNull();
    expect(component.status()?.status).toBe("disabled");
  });

  it("restores the top bar on destroy", () => {
    const fixture = TestBed.createComponent(WorkflowsPageComponent);

    fixture.destroy();

    expect(chrome.hideTopbar).toHaveBeenCalled();
    expect(chrome.showTopbar).toHaveBeenCalled();
  });
});
