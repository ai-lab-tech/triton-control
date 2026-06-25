import { TestBed } from "@angular/core/testing";
import { HttpClientTestingModule, HttpTestingController } from "@angular/common/http/testing";
import { provideRouter } from "@angular/router";

import { BASE_PATH } from "../../api/generated/index";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";
import { MlflowPageComponent } from "./mlflow-page.component";

describe("MlflowPageComponent", () => {
  let auth: jasmine.SpyObj<AuthService>;
  let chrome: jasmine.SpyObj<ChromeService>;
  let http: HttpTestingController;
  const notInstalledStatus = {
    installed: false,
    status: "not_installed",
    ready: false,
    status_message: "",
    base_path: "/api/mlflow/proxy/",
    installation: null,
  };

  beforeEach(async () => {
    auth = jasmine.createSpyObj<AuthService>("AuthService", ["refreshSession"]);
    chrome = jasmine.createSpyObj<ChromeService>("ChromeService", ["hideTopbar", "showTopbar"]);
    auth.refreshSession.and.resolveTo();

    await TestBed.configureTestingModule({
      imports: [MlflowPageComponent, HttpClientTestingModule],
      providers: [
        provideRouter([]),
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

  function flushInitialStatus(status = notInstalledStatus): void {
    http.expectOne("/api/mlflow").flush(status);
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

  it("opens the proxied MLflow server in a new tab", async () => {
    const fixture = TestBed.createComponent(MlflowPageComponent);
    const component = fixture.componentInstance;

    await flushMicrotasks();
    const req = http.expectOne("/api/mlflow");
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

    const openSpy = spyOn(window, "open").and.returnValue(null);
    component.openInNewTab();

    expect(component.frameRawUrl()).toBe("/api/mlflow/proxy/");
    expect(openSpy).toHaveBeenCalledWith("/api/mlflow/proxy/", "_blank", "noopener");
  });

  it("can install when fields are present and not installed", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    flushInitialStatus();
    component.installationName = "mlflow";
    component.image = "ghcr.io/mlflow/mlflow:v2.15.1";

    expect(component.canInstall()).toBeTrue();
  });

  it("restores top bar on destroy", async () => {
    const fixture = TestBed.createComponent(MlflowPageComponent);
    await flushMicrotasks();
    flushInitialStatus();

    fixture.destroy();

    expect(chrome.hideTopbar).toHaveBeenCalled();
    expect(chrome.showTopbar).toHaveBeenCalled();
  });

  it("validates pull secret payload", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    flushInitialStatus();

    expect(component.pullSecretStatus().label).toBe("Not configured");

    component.dockerconfigjson = '{"auths":{"registry.example.com":{"auth":"token"}}}';
    expect(component.pullSecretStatus().label).toBe("Configured");

    component.dockerconfigjson = '{"auths":{}}';
    expect(component.pullSecretStatus().label).toBe("Invalid");

    component.dockerconfigjson = "{broken";
    expect(component.pullSecretStatus().detail).toContain("Invalid JSON");
  });

  it("installs and reloads status", async () => {
    const fixture = TestBed.createComponent(MlflowPageComponent);
    const component = fixture.componentInstance;
    await flushMicrotasks();
    flushInitialStatus();
    await flushMicrotasks();

    component.installationName = "mlflow";
    component.image = "ghcr.io/mlflow/mlflow:v2.15.1";
    expect(component.canInstall()).toBeTrue();
    const installPromise = component.install();
    await flushMicrotasks();

    const installReq = http.expectOne("/api/mlflow");
    expect(installReq.request.method).toBe("POST");
    expect(installReq.request.body.installation_name).toBe("mlflow");
    installReq.flush({
      namespace: "triton-control",
      deployment_name: "mlflow",
      service_name: "mlflow-service",
      image: "ghcr.io/mlflow/mlflow:v2.15.1",
      applied_resources: ["Deployment/mlflow"],
    });
    await flushMicrotasks();

    http.expectOne("/api/mlflow").flush({
      installed: true,
      status: "ready",
      ready: true,
      status_message: "ready",
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
    await installPromise;

    expect(component.messageTone()).toBe("success");
    expect(component.frameUrl()).not.toBeNull();
  });

  it("handles install failure", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    flushInitialStatus();
    await flushMicrotasks();

    component.installationName = "mlflow";
    component.image = "ghcr.io/mlflow/mlflow:v2.15.1";
    expect(component.canInstall()).toBeTrue();
    const installPromise = component.install();
    await flushMicrotasks();
    const installReq = http.expectOne("/api/mlflow");
    installReq.flush({ message: "boom" }, { status: 500, statusText: "Server Error" });
    await installPromise;

    expect(component.messageTone()).toBe("error");
  });

  it("reloads frame when status is ready", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    http.expectOne("/api/mlflow").flush({
      installed: true,
      status: "ready",
      ready: true,
      status_message: "ready",
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
    const first = `${component.frameUrl()}`;

    component.reload();
    const second = `${component.frameUrl()}`;

    expect(second).not.toEqual(first);
    expect(second).toContain("_tc_reload=");
  });

  it("uninstalls when confirmed", async () => {
    const component = TestBed.createComponent(MlflowPageComponent).componentInstance;
    await flushMicrotasks();
    http.expectOne("/api/mlflow").flush({
      installed: true,
      status: "ready",
      ready: true,
      status_message: "ready",
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
    spyOn(window, "confirm").and.returnValue(true);

    const uninstallPromise = component.uninstall();
    await flushMicrotasks();
    const uninstallReq = http.expectOne("/api/mlflow");
    expect(uninstallReq.request.method).toBe("DELETE");
    uninstallReq.flush({
      status: "deleted",
      message: "MLflow uninstalled",
      namespace: "triton-control",
    });
    await uninstallPromise;

    expect(component.status()?.installed).toBeFalse();
    expect(component.frameUrl()).toBeNull();
    expect(component.messageTone()).toBe("success");
  });
});
