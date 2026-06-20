import { TestBed } from "@angular/core/testing";
import { HttpClientTestingModule, HttpTestingController } from "@angular/common/http/testing";

import { BASE_PATH } from "../../api/generated/index";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";
import { MlflowPageComponent } from "./mlflow-page.component";

describe("MlflowPageComponent", () => {
  let auth: jasmine.SpyObj<AuthService>;
  let chrome: jasmine.SpyObj<ChromeService>;
  let http: HttpTestingController;

  beforeEach(async () => {
    auth = jasmine.createSpyObj<AuthService>("AuthService", ["refreshSession"]);
    chrome = jasmine.createSpyObj<ChromeService>("ChromeService", ["hideTopbar", "showTopbar"]);
    auth.refreshSession.and.resolveTo();

    await TestBed.configureTestingModule({
      imports: [MlflowPageComponent, HttpClientTestingModule],
      providers: [
        { provide: AuthService, useValue: auth },
        { provide: ChromeService, useValue: chrome },
        { provide: BASE_PATH, useValue: "" },
      ],
    }).compileComponents();

    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  async function flushMicrotasks(times = 3): Promise<void> {
    for (let i = 0; i < times; i += 1) {
      await Promise.resolve();
    }
  }

  it("loads status and embeds iframe when ready", async () => {
    const fixture = TestBed.createComponent(MlflowPageComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    const req = http.expectOne("/api/mlflow");
    expect(req.request.method).toBe("GET");
    req.flush({
      installed: true,
      status: "ready",
      ready: true,
      status_message: "ok",
      base_path: "/api/mlflow/proxy/",
      installation: {
        namespace: "triton-control",
        deployment_name: "mlflow",
        service_name: "mlflow-service",
        image: "ghcr.io/mlflow/mlflow:v2.15.1",
        applied_resources: ["Deployment/mlflow"],
      },
    });
    await flushMicrotasks();

    expect(component.frameUrl()).not.toBeNull();
    expect(`${component.frameUrl()}`).toContain("/api/mlflow/proxy/?_tc_reload=");
  });

  it("can install when fields are present and not installed", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    http.expectOne("/api/mlflow").flush({
      installed: false,
      status: "not_installed",
      ready: false,
      status_message: "",
      base_path: "/api/mlflow/proxy/",
      installation: null,
    });
    component.installationName = "mlflow";
    component.image = "ghcr.io/mlflow/mlflow:v2.15.1";

    expect(component.canInstall()).toBeTrue();
  });

  it("restores top bar on destroy", async () => {
    const fixture = TestBed.createComponent(MlflowPageComponent);
    await flushMicrotasks();
    http.expectOne("/api/mlflow").flush({
      installed: false,
      status: "not_installed",
      ready: false,
      status_message: "",
      base_path: "/api/mlflow/proxy/",
      installation: null,
    });

    fixture.destroy();

    expect(chrome.hideTopbar).toHaveBeenCalled();
    expect(chrome.showTopbar).toHaveBeenCalled();
  });
});
