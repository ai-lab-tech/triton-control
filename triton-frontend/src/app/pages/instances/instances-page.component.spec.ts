/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { NavigationEnd, Router } from "@angular/router";
import { of, Subject } from "rxjs";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { InstancesPageComponent } from "./instances-page.component";
import { InstancesService } from "../../api/generated/index";
import { AuthStore } from "../../shared/auth/auth.store";
import { selectInstances } from "../../state/instances-list/instances-list.selectors";
import {
  deleteInstanceRequested,
  instancesListRefreshRequested,
} from "../../state/instances-list/instances-list.actions";
import { dtoToInstance } from "../../state/instances.utils";

describe("InstancesPageComponent", () => {
  const events$ = new Subject<any>();
  const routerMock = {
    url: "/instances",
    events: events$.asObservable(),
  };
  const instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
    "listInstancesApiInstancesGet",
    "createInstanceApiInstancesPost",
  ]);
  let mockStore: MockStore;
  let authState: InstanceType<typeof AuthStore>;

  beforeEach(async () => {
    instancesApiMock.listInstancesApiInstancesGet.and.returnValue(of([]) as any);
    instancesApiMock.createInstanceApiInstancesPost.and.returnValue(of({ id: 1 } as any) as any);
    await TestBed.configureTestingModule({
      imports: [InstancesPageComponent],
      providers: [
        provideMockStore(),
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: Router, useValue: routerMock },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
    authState = TestBed.inject(AuthStore);
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);

    // Act
    const instance = fixture.componentInstance;

    // Assert
    expect(instance).toBeTruthy();
  });

  it("FilteredInstances_QueryAndStatusSet_ReturnsMatchingRows", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance;

    mockStore.overrideSelector(selectInstances, [
      {
        id: "1",
        name: "Triton EU",
        url: "https://example.local",
        status: "healthy",
        version: "",
        region: "Europe",
        models: 2,
        healthLive: true,
        healthReady: true,
        healthLastCheckedAt: "",
        healthError: "",
        serverMetadata: null,
        qps: 0,
        cpu: 0,
        ram: 0,
        gpu: 0,
        assignedUsers: [],
        s3: {
          enabled: false,
          bucket: "",
          region: "",
          endpoint: "",
          prefix: "",
          accessKey: "",
          secretConfigured: false,
        },
        modelFiles: [],
        repositoryModels: [],
      },
      {
        id: "2",
        name: "Node US",
        url: "https://us.local",
        status: "down",
        version: "",
        region: "US",
        models: 1,
        healthLive: false,
        healthReady: false,
        healthLastCheckedAt: "",
        healthError: "",
        serverMetadata: null,
        qps: 0,
        cpu: 0,
        ram: 0,
        gpu: 0,
        assignedUsers: [],
        s3: {
          enabled: false,
          bucket: "",
          region: "",
          endpoint: "",
          prefix: "",
          accessKey: "",
          secretConfigured: false,
        },
        modelFiles: [],
        repositoryModels: [],
      },
    ] as any);
    mockStore.refreshState();

    component.query = "eu";
    component.status = "healthy";

    // Act
    const filtered = component.filteredInstances();

    // Assert
    expect(filtered.length).toBe(1);
    expect(filtered[0].id).toBe("1");
  });

  it("DtoToInstance_ReadyLiveButNotReadyHealth_MapsWarningAndMetadataVersion", () => {
    // Act
    const mapped = dtoToInstance({
      id: 11,
      name: "node-11",
      url: "http://node11",
      model_names: ["m1"],
      health_live: true,
      health_ready: false,
      server_metadata: { version: "3.1.0" },
      s3: {
        enabled: true,
        bucket: "b",
        region: "r",
        endpoint: "e",
        prefix: "p",
        accessKey: "ak",
        secretConfigured: true,
      },
    } as any);

    // Assert
    expect(mapped.status).toBe("warning");
    expect(mapped.version).toBe("3.1.0");
    expect(mapped.models).toBe(1);
    expect(mapped.s3.enabled).toBeTrue();
  });

  it("DeploymentStarting_SelfDeployedPodStillPending_ReturnsTrue", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance;

    // Act
    const starting = component.deploymentStarting({
      id: "3",
      name: "self-deployed",
      url: "http://self-deployed",
      status: "down",
      version: "",
      region: "Unknown",
      models: 0,
      healthLive: false,
      healthReady: false,
      healthLastCheckedAt: "",
      healthError: "Waiting for pod to become ready...",
      isSelfDeployed: true,
    } as any);

    // Assert
    expect(starting).toBeTrue();
  });

  it("Refresh_Called_DispatchesRefreshRequestedAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch").and.callThrough();

    // Act
    component.refresh();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(instancesListRefreshRequested());
  });

  it("DeleteInstance_AdminConfirms_DispatchesDeleteRequestedAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Admin", role: "admin", accessAllowed: true });
    spyOn(window, "confirm").and.returnValue(true);
    spyOn(mockStore, "dispatch");

    // Act
    component.deleteInstance("7", "node-7");

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      deleteInstanceRequested({ instanceId: "7", instanceName: "node-7", isSelfDeployed: false }),
    );
  });

  it("DeleteInstance_MemberRole_DoesNotDispatch", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });
    spyOn(window, "confirm");
    spyOn(mockStore, "dispatch");

    // Act
    component.deleteInstance("7", "node-7");

    // Assert
    expect(window.confirm).not.toHaveBeenCalled();
    expect(mockStore.dispatch).not.toHaveBeenCalled();
  });

  it("NgOnInit_NavigationEndOnInstancesRoute_ResetsQueryAndStatus", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstancesPageComponent);
    const component = fixture.componentInstance as any;
    spyOn(component, "refresh").and.callThrough();
    component.query = "x";
    component.status = "down";

    // Act
    await component.ngOnInit();
    events$.next(new NavigationEnd(1, "/instances", "/instances"));
    await Promise.resolve();

    // Assert
    expect(component.query).toBe("");
    expect(component.status).toBe("all");
  });
});
