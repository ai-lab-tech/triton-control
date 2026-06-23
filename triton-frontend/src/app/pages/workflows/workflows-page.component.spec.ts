import { TestBed } from "@angular/core/testing";
import { of } from "rxjs";
import { MatDialog } from "@angular/material/dialog";

import { BASE_PATH, WorkflowsService } from "../../api/generated/index";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";
import { WorkflowsPageComponent } from "./workflows-page.component";
import { S3CredentialsDialogComponent } from "./s3-credentials-dialog/s3-credentials-dialog.component";

describe("WorkflowsPageComponent", () => {
  let workflowsApi: jasmine.SpyObj<WorkflowsService>;
  let auth: jasmine.SpyObj<AuthService>;
  let chrome: jasmine.SpyObj<ChromeService>;
  let dialog: jasmine.SpyObj<MatDialog>;

  beforeEach(async () => {
    workflowsApi = jasmine.createSpyObj<WorkflowsService>("WorkflowsService", [
      "getArgoWorkflowsStatusApiWorkflowsGet",
    ]);
    auth = jasmine.createSpyObj<AuthService>("AuthService", ["refreshSession"]);
    chrome = jasmine.createSpyObj<ChromeService>("ChromeService", ["hideTopbar", "showTopbar"]);
    dialog = jasmine.createSpyObj<MatDialog>("MatDialog", ["open"]);

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
        { provide: MatDialog, useValue: dialog },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();
  });

  async function flushMicrotasks(times = 3): Promise<void> {
    for (let i = 0; i < times; i += 1) {
      await Promise.resolve();
    }
  }

  it("loads status and embeds the ready Argo server", async () => {
    const fixture = TestBed.createComponent(WorkflowsPageComponent);
    const component = fixture.componentInstance;
    await flushMicrotasks();

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
    await flushMicrotasks();

    expect(component.frameUrl()).toBeNull();
    expect(component.status()?.status).toBe("disabled");
  });

  it("opens configure s3 secrets dialog", () => {
    const fixture = TestBed.createComponent(WorkflowsPageComponent);
    const component = fixture.componentInstance;
    (component as unknown as { dialog: MatDialog }).dialog = dialog;

    component.openCredentialsDialog();

    expect(dialog.open).toHaveBeenCalledWith(
      S3CredentialsDialogComponent,
      jasmine.objectContaining({
        width: "900px",
        maxWidth: "95vw",
      }),
    );
  });

  it("restores the top bar on destroy", () => {
    const fixture = TestBed.createComponent(WorkflowsPageComponent);
    fixture.destroy();

    expect(chrome.hideTopbar).toHaveBeenCalled();
    expect(chrome.showTopbar).toHaveBeenCalled();
  });
});
