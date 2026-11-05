import { TestBed } from "@angular/core/testing";
import { provideNoopAnimations } from "@angular/platform-browser/animations";
import { of, throwError } from "rxjs";
import { DeploymentAccessResponse, DeploymentsService } from "../../../api/generated";
import { DeploymentAccessComponent } from "./deployment-access.component";

describe("DeploymentAccessComponent", () => {
  let api: jasmine.SpyObj<DeploymentsService>;
  const ingress = {
    access_mode: "ingress",
    url: "https://model.example.com",
    internal_url: "http://model.control:18000",
    ingress_host: "model.example.com",
    hostname: "model",
    ingress_scheme: "https",
    basic_auth_enabled: true,
  };

  beforeEach(() => {
    api = jasmine.createSpyObj<DeploymentsService>("DeploymentsService", [
      "getDeploymentPolicyApiDeploymentsPolicyGet",
      "getDeploymentAccessApiDeploymentsInstanceIdAccessGet",
      "updateDeploymentAccessApiDeploymentsInstanceIdAccessPut",
    ]);
    api.getDeploymentAccessApiDeploymentsInstanceIdAccessGet.and.returnValue(
      of(ingress) as unknown as ReturnType<
        DeploymentsService["getDeploymentAccessApiDeploymentsInstanceIdAccessGet"]
      >,
    );
    api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut.and.returnValue(
      of(ingress) as unknown as ReturnType<
        DeploymentsService["updateDeploymentAccessApiDeploymentsInstanceIdAccessPut"]
      >,
    );
    api.getDeploymentPolicyApiDeploymentsPolicyGet.and.returnValue(
      of({
        ingress_enabled: true,
        allow_http: false,
        default_allow_http: false,
        source: "environment",
      }) as unknown as ReturnType<DeploymentsService["getDeploymentPolicyApiDeploymentsPolicyGet"]>,
    );
    TestBed.configureTestingModule({
      imports: [DeploymentAccessComponent],
      providers: [provideNoopAnimations(), { provide: DeploymentsService, useValue: api }],
    });
  });

  function create(canWrite = true) {
    const fixture = TestBed.createComponent(DeploymentAccessComponent);
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("canWrite", canWrite);
    fixture.componentInstance.ingressEnabled.set(true);
    return fixture;
  }

  it("loads the exposed endpoint without showing credentials", async () => {
    const fixture = create();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("https://model.example.com");
    expect(fixture.nativeElement.querySelector('input[type="password"]')).toBeNull();
  });

  it("switches to internal access without sending stale credentials or host", async () => {
    const component = create().componentInstance;
    await component.load();
    component.edit();
    component.username = "alice";
    component.password = "secret";
    component.mode = "internal";
    await component.save();
    expect(api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut).toHaveBeenCalledWith(
      {
        access_mode: "internal",
        ingress_host: undefined,
        ingress_scheme: undefined,
        basic_auth_enabled: false,
        basic_auth_username: undefined,
        basic_auth_password: undefined,
      },
      "7",
    );
    expect(component.password).toBe("");
  });

  it("keeps existing credentials when both fields are blank", async () => {
    const component = create().componentInstance;
    await component.load();
    component.edit();
    expect(component.valid()).toBeTrue();
    await component.save();
    expect(
      api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut.calls.mostRecent().args[0],
    ).toEqual(
      jasmine.objectContaining({ basic_auth_enabled: true, basic_auth_password: undefined }),
    );
  });

  it("requires replacement credentials only after choosing Change credentials", async () => {
    const fixture = create();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.componentInstance.edit();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('input[type="password"]')).toBeNull();
    const changeButton = Array.from(fixture.nativeElement.querySelectorAll("button")).find(
      (button) => (button as HTMLElement).textContent?.includes("Change credentials"),
    ) as HTMLButtonElement;
    changeButton.click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('input[type="password"]')).not.toBeNull();
    expect(fixture.componentInstance.valid()).toBeFalse();
    fixture.componentInstance.username = "alice";
    fixture.componentInstance.password = "replacement";
    expect(fixture.componentInstance.valid()).toBeTrue();
    await fixture.componentInstance.save();
    expect(
      api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut.calls.mostRecent().args[0],
    ).toEqual(
      jasmine.objectContaining({
        basic_auth_username: "alice",
        basic_auth_password: "replacement",
      }),
    );
  });

  it("requires both credentials for new auth and rejects HTTP auth", async () => {
    const component = create().componentInstance;
    component.access.set({ ...ingress, basic_auth_enabled: false });
    component.edit();
    component.authEnabled = true;
    expect(component.valid()).toBeFalse();
    component.username = "alice";
    expect(component.valid()).toBeFalse();
    component.password = "secret";
    expect(component.valid()).toBeTrue();
    component.scheme = "http";
    expect(component.valid()).toBeFalse();
    await component.save();
    expect(api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut).not.toHaveBeenCalled();
  });

  it("clears passwords and reports failed saves without claiming success", async () => {
    const component = create().componentInstance;
    await component.load();
    component.edit();
    component.username = "alice";
    component.password = "secret";
    api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut.and.returnValue(
      throwError(() => new Error("Unavailable")),
    );
    await component.save();
    expect(component.error()).toBeTruthy();
    expect(component.message()).toBe("");
    expect(component.password).toBe("");
    expect(component.editing()).toBeTrue();
  });

  it("blocks new HTTP hosts but preserves an existing HTTP endpoint", () => {
    const component = create().componentInstance;
    component.access.set({
      ...ingress,
      basic_auth_enabled: false,
      ingress_scheme: "http",
      ingress_host: "model",
    });
    component.edit();
    expect(component.valid()).toBeTrue();
    component.host = "new";
    expect(component.valid()).toBeFalse();
    component.allowHttp.set(true);
    expect(component.valid()).toBeTrue();
  });

  it("disables ingress edits but permits cluster-only access when policy is disabled", async () => {
    const component = create().componentInstance;
    await component.load();
    component.ingressEnabled.set(false);
    component.edit();
    expect(component.valid()).toBeFalse();
    component.mode = "internal";
    expect(component.valid()).toBeTrue();
  });

  it("edits the short hostname and previews the configured domain", () => {
    const component = create().componentInstance;
    component.baseDomain.set("models.example.com");
    component.access.set({
      ...ingress,
      hostname: "smollm",
      ingress_host: "smollm.models.example.com",
    });
    component.edit();
    expect(component.host).toBe("smollm");
    expect(component.endpointPreview()).toBe("https://smollm.models.example.com");
    expect(component.valid()).toBeTrue();
    component.host = "smollm.other.de";
    expect(component.valid()).toBeFalse();
  });

  it("rejects a full domain even without a configured base domain", () => {
    // Arrange
    const component = create().componentInstance;
    component.access.set(ingress);
    component.edit();
    component.host = "model.example.com";
    // Act
    const valid = component.valid();
    // Assert
    expect(valid).toBeFalse();
  });

  it("does not allow viewers to edit or save", async () => {
    const component = create(false).componentInstance;
    await component.load();
    component.edit();
    expect(component.editing()).toBeFalse();
    await component.save();
    expect(api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut).not.toHaveBeenCalled();
  });
  it("keeps the existing domain in the preview after an admin domain change", () => {
    // Arrange
    const component = create().componentInstance;
    component.access.set(ingress as DeploymentAccessResponse);
    component.baseDomain.set("new.example");
    // Act
    component.edit();
    // Assert
    expect(component.endpointPreview()).toBe("https://model.example.com");
  });

  it("allows existing HTTP after a domain change while new HTTP is disabled", () => {
    // Arrange
    const component = create().componentInstance;
    component.access.set({
      ...ingress,
      ingress_scheme: "http",
      basic_auth_enabled: false,
    } as DeploymentAccessResponse);
    component.baseDomain.set("new.example");
    component.allowHttp.set(false);
    // Act
    component.edit();
    // Assert
    expect(component.valid()).toBeTrue();
    expect(component.endpointPreview()).toBe("http://model.example.com");
  });

  it("uses the configured domain when the user changes the short hostname", () => {
    // Arrange
    const component = create().componentInstance;
    component.access.set(ingress as DeploymentAccessResponse);
    component.baseDomain.set("new.example");
    component.edit();
    // Act
    component.host = "renamed";
    // Assert
    expect(component.endpointPreview()).toBe("https://renamed.new.example");
  });
});
