import { TestBed } from "@angular/core/testing";
import { provideRouter } from "@angular/router";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { of } from "rxjs";

import { PerfAnalyzersService } from "../../api/generated/index";
import { type Instance } from "../instances/instances.data";
import { instancesListRefreshRequested } from "../../state/instances-list/instances-list.actions";
import { NewPerfAnalyzerPageComponent } from "./new-perf-analyzer-page.component";

describe("NewPerfAnalyzerPageComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NewPerfAnalyzerPageComponent],
      providers: [
        provideRouter([]),
        provideMockStore(),
        {
          provide: PerfAnalyzersService,
          useValue: jasmine.createSpyObj<PerfAnalyzersService>("PerfAnalyzersService", [
            "getPerfAnalyzerStatusApiPerfAnalyzersGet",
            "installPerfAnalyzerApiPerfAnalyzersPost",
            "uninstallPerfAnalyzerApiPerfAnalyzersDelete",
          ]),
        },
      ],
    }).compileComponents();
  });

  it("CanInstall_RequiredFieldsProvided_ReturnsTrue", () => {
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    component.installationName = "perf";
    component.image = "custom/perf:latest";
    expect(component.canInstall()).toBeTrue();
  });

  it("OnInit_PageOpened_RefreshesTritonInstances", () => {
    const store = TestBed.inject(MockStore);
    spyOn(store, "dispatch");
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    const perfAnalyzersApi = TestBed.inject(
      PerfAnalyzersService,
    ) as jasmine.SpyObj<PerfAnalyzersService>;
    perfAnalyzersApi.getPerfAnalyzerStatusApiPerfAnalyzersGet.and.returnValue(
      of({ installed: false }) as unknown as ReturnType<
        PerfAnalyzersService["getPerfAnalyzerStatusApiPerfAnalyzersGet"]
      >,
    );

    component.ngOnInit();

    expect(store.dispatch).toHaveBeenCalledWith(instancesListRefreshRequested());
  });

  it("ProfileTarget_ReadyModelAvailable_ReturnsFirstProfilableModel", () => {
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    const instance = {
      repositoryModels: [
        { name: "pending", version: "1", state: "UNAVAILABLE", reason: "" },
        { name: "resnet", version: "2", state: "READY", reason: "" },
      ],
    } as Instance;

    expect(component.profileTarget(instance)).toEqual(
      jasmine.objectContaining({ name: "resnet", version: "2" }),
    );
  });

  it("Install_DockerConfigProvided_SendsImagePullSecretInput", async () => {
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    const perfAnalyzersApi = TestBed.inject(
      PerfAnalyzersService,
    ) as jasmine.SpyObj<PerfAnalyzersService>;
    perfAnalyzersApi.installPerfAnalyzerApiPerfAnalyzersPost.and.returnValue(
      of({
        namespace: "perf",
        deployment_name: "perf",
        image: "custom/perf:latest",
        applied_resources: ["Deployment/perf"],
      }) as unknown as ReturnType<PerfAnalyzersService["installPerfAnalyzerApiPerfAnalyzersPost"]>,
    );
    component.installationName = "perf";
    component.image = "custom/perf:latest";
    component.dockerconfigjson.set('{"auths":{"registry.example":{"auth":"token"}}}');

    await component.install();

    expect(perfAnalyzersApi.installPerfAnalyzerApiPerfAnalyzersPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        installation_name: "perf",
        image: "custom/perf:latest",
        dockerconfigjson: '{"auths":{"registry.example":{"auth":"token"}}}',
      }),
    );
  });

  it("LoadStatus_InstalledAnalyzerReturned_StoresInstallation", async () => {
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    const perfAnalyzersApi = TestBed.inject(
      PerfAnalyzersService,
    ) as jasmine.SpyObj<PerfAnalyzersService>;
    perfAnalyzersApi.getPerfAnalyzerStatusApiPerfAnalyzersGet.and.returnValue(
      of({
        installed: true,
        installation: {
          namespace: "perf",
          deployment_name: "perf",
          image: "custom/perf:latest",
          applied_resources: ["Deployment/perf"],
        },
      }) as unknown as ReturnType<PerfAnalyzersService["getPerfAnalyzerStatusApiPerfAnalyzersGet"]>,
    );

    await component.loadStatus();

    expect(component.installation()?.namespace).toBe("perf");
    expect(component.canInstall()).toBeFalse();
  });

  it("Uninstall_Confirmed_ClearsInstallation", async () => {
    const component = TestBed.createComponent(NewPerfAnalyzerPageComponent).componentInstance;
    const perfAnalyzersApi = TestBed.inject(
      PerfAnalyzersService,
    ) as jasmine.SpyObj<PerfAnalyzersService>;
    spyOn(window, "confirm").and.returnValue(true);
    perfAnalyzersApi.uninstallPerfAnalyzerApiPerfAnalyzersDelete.and.returnValue(
      of({
        status: "deleted",
        message: "Namespace deletion requested.",
        namespace: "perf",
      }) as unknown as ReturnType<
        PerfAnalyzersService["uninstallPerfAnalyzerApiPerfAnalyzersDelete"]
      >,
    );
    component.installation.set({
      namespace: "perf",
      deployment_name: "perf",
      image: "custom/perf:latest",
      applied_resources: ["Deployment/perf"],
    });

    await component.uninstall();

    expect(perfAnalyzersApi.uninstallPerfAnalyzerApiPerfAnalyzersDelete).toHaveBeenCalled();
    expect(component.installation()).toBeNull();
  });
});
