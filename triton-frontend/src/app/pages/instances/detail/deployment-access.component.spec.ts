import { TestBed } from "@angular/core/testing";
import { provideNoopAnimations } from "@angular/platform-browser/animations";
import { of, throwError } from "rxjs";
import { DeploymentsService } from "../../../api/generated";
import { DeploymentAccessComponent } from "./deployment-access.component";

describe("DeploymentAccessComponent", () => {
  let api: jasmine.SpyObj<DeploymentsService>;
  const ingress = {
    access_mode: "ingress",
    url: "https://model.example.com",
    internal_url: "http://model.control:18000",
    ingress_host: "model.example.com",
    ingress_scheme: "https",
    basic_auth_enabled: true,
  };

  beforeEach(() => {
    api = jasmine.createSpyObj<DeploymentsService>("DeploymentsService", [
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
    TestBed.configureTestingModule({
      imports: [DeploymentAccessComponent],
      providers: [provideNoopAnimations(), { provide: DeploymentsService, useValue: api }],
    });
  });

  function create(canWrite = true) {
    const fixture = TestBed.createComponent(DeploymentAccessComponent);
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("canWrite", canWrite);
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

  it("does not allow viewers to edit or save", async () => {
    const component = create(false).componentInstance;
    await component.load();
    component.edit();
    expect(component.editing()).toBeFalse();
    await component.save();
    expect(api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut).not.toHaveBeenCalled();
  });
});
