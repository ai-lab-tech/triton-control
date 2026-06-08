/* eslint-disable @typescript-eslint/no-explicit-any */
import { BreakpointObserver } from "@angular/cdk/layout";
import { TestBed } from "@angular/core/testing";
import { MatDialog } from "@angular/material/dialog";
import { Router } from "@angular/router";
import { of, Subject } from "rxjs";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { InstancesService } from "../api/generated/index";
import { AuthStore } from "../shared/auth/auth.store";
import { AuthService } from "../shared/auth/auth.service";
import { ShellComponent } from "./shell.component";
import { selectDashboardFleetHealthPercentage } from "../state/dashboard/dashboard.selectors";

describe("ShellComponent", () => {
  let routerEvents$: Subject<any>;
  let routerMock: jasmine.SpyObj<Router>;
  let dialogMock: jasmine.SpyObj<MatDialog>;
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let authState: InstanceType<typeof AuthStore>;
  let mockStore: MockStore;

  beforeEach(async () => {
    routerEvents$ = new Subject<any>();
    routerMock = jasmine.createSpyObj<Router>("Router", ["navigateByUrl"], {
      url: "/instances",
      events: routerEvents$.asObservable(),
    });
    routerMock.navigateByUrl.and.resolveTo(true);
    dialogMock = jasmine.createSpyObj<MatDialog>("MatDialog", ["open"]);
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", [
      "login",
      "logout",
      "getOidcSettings",
    ]);
    authServiceMock.getOidcSettings.and.resolveTo({
      oidcEnabled: false,
      issuer: "",
      clientId: "",
      clientSecret: "",
      clientSecretConfigured: false,
      redirectUri: "",
      scopes: "openid profile email",
      strictDiscoveryDocumentValidation: false,
      caCertificate: "",
      apiBaseUrl: "http://127.0.0.1:8000",
      configSource: "db",
      kubernetesEnabled: false,
    });
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "listInstancesApiInstancesGet",
    ]);
    instancesApiMock.listInstancesApiInstancesGet.and.returnValue(of([]) as any);

    await TestBed.configureTestingModule({
      imports: [ShellComponent],
      providers: [
        provideMockStore(),
        AuthStore,
        { provide: Router, useValue: routerMock },
        { provide: MatDialog, useValue: dialogMock },
        { provide: AuthService, useValue: authServiceMock },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: BreakpointObserver, useValue: { observe: () => of({ matches: false }) } },
      ],
    }).compileComponents();

    authState = TestBed.inject(AuthStore);
    mockStore = TestBed.inject(MockStore);
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("Go_PathProvided_NavigatesAndClosesAdminMenu", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act
    component.go("/dashboard");

    // Assert
    expect(routerMock.navigateByUrl).toHaveBeenCalledWith("/dashboard");
    expect(component.adminMenuOpen()).toBeFalse();
  });

  it("OpenNewInstanceDialog_MethodInvoked_OpensDialogWithExpectedConfig", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    (component as any).dialog = dialogMock;
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });

    // Act
    component.openNewInstanceDialog();

    // Assert
    expect(dialogMock.open).toHaveBeenCalled();
  });

  it("OpenNewInstanceDialog_ViewerRole_DoesNotOpenDialog", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Viewer", role: "viewer", accessAllowed: true });

    // Act
    component.openNewInstanceDialog();

    // Assert
    expect(dialogMock.open).not.toHaveBeenCalled();
  });

  it("FleetHealth_UserLoggedInAndRowsReturned_ComputesHealthPercentage", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardFleetHealthPercentage, 50);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Admin", role: "admin", accessAllowed: true });

    // Act + Assert
    expect(component.fleetHealthPercentage()).toBe(50);
  });

  it("NavItems_KubernetesDisabled_ShowsDisabledKubernetesActions", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });
    component.kubernetesCapabilityLoaded.set(true);
    component.kubernetesEnabled.set(false);

    // Act
    const addDeployment = component.navItems().find((item) => item.path === "/deployments/new");
    const perfAnalyzer = component.navItems().find((item) => item.path === "/perf-analyzers");

    // Assert
    expect(addDeployment?.disabledReason).toContain("Triton Control itself runs in Kubernetes");
    expect(perfAnalyzer?.disabledReason).toContain("Triton Control itself runs in Kubernetes");
  });

  it("NavItems_KubernetesEnabled_ShowsEnabledKubernetesActions", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });
    component.kubernetesCapabilityLoaded.set(true);
    component.kubernetesEnabled.set(true);

    // Act
    const addDeployment = component.navItems().find((item) => item.path === "/deployments/new");
    const perfAnalyzer = component.navItems().find((item) => item.path === "/perf-analyzers");

    // Assert
    expect(addDeployment?.disabledReason).toBeUndefined();
    expect(perfAnalyzer?.disabledReason).toBeUndefined();
  });

  it("ToggleAdminMenu_InvokedTwice_TogglesOpenState", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act
    component.toggleAdminMenu();
    component.toggleAdminMenu();

    // Assert
    expect(component.adminMenuOpen()).toBeFalse();
  });

  it("CloseAdminMenu_AdminMenuOpen_ClosesAdminMenu", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    component.adminMenuOpen.set(true);

    // Act
    component.closeAdminMenu();

    // Assert
    expect(component.adminMenuOpen()).toBeFalse();
  });

  it("Login_MethodInvoked_CallsAuthLoginAndClosesAdminMenu", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    component.adminMenuOpen.set(true);

    // Act
    component.login();

    // Assert
    expect(authServiceMock.login).toHaveBeenCalled();
    expect(component.adminMenuOpen()).toBeFalse();
  });

  it("Logout_MethodInvoked_CallsAuthLogoutAndNavigatesToLogin", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    component.adminMenuOpen.set(true);

    // Act
    component.logout();

    // Assert
    expect(authServiceMock.logout).toHaveBeenCalled();
    expect(routerMock.navigateByUrl).toHaveBeenCalledWith("/signin");
    expect(component.adminMenuOpen()).toBeFalse();
  });

  it("FleetHealth_HealthPercentageIsNull_ReturnsNullFromSignal", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardFleetHealthPercentage, null);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act + Assert
    expect(component.fleetHealthPercentage()).toBeNull();
  });

  it("FleetHealthLabel_HealthPercentageAvailable_ReturnsFormattedLabel", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardFleetHealthPercentage, 75);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Admin", role: "admin", accessAllowed: true });

    // Act + Assert
    expect(component.fleetHealthLabel()).toBe("Fleet health 75%");
  });

  it("FleetHealthLabel_UserLoggedOut_ReturnsNotAvailableLabel", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardFleetHealthPercentage, null);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act
    const label = component.fleetHealthLabel();

    // Assert
    expect(label).toBe("Fleet health n/a");
  });

  it("Initials_UserNameHasTwoParts_ReturnsInitialPair", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Jane Doe", accessAllowed: true });

    // Act
    const initials = component.initials();

    // Assert
    expect(initials).toBe("JD");
  });

  it("CloseNavOnMobile_DesktopLayout_LeavesNavigationStateUnchanged", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    component.navOpen.set(true);

    // Act
    component.closeNavOnMobile();

    // Assert
    expect(component.navOpen()).toBeTrue();
  });
});

describe("ShellComponentHandset", () => {
  let routerMock: jasmine.SpyObj<Router>;
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let instancesApiMock: jasmine.SpyObj<InstancesService>;

  beforeEach(async () => {
    routerMock = jasmine.createSpyObj<Router>("Router", ["navigateByUrl"], {
      url: "/instances",
      events: of(),
    });
    routerMock.navigateByUrl.and.resolveTo(true);
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", [
      "login",
      "logout",
      "getOidcSettings",
    ]);
    authServiceMock.getOidcSettings.and.resolveTo({
      oidcEnabled: false,
      issuer: "",
      clientId: "",
      clientSecret: "",
      clientSecretConfigured: false,
      redirectUri: "",
      scopes: "openid profile email",
      strictDiscoveryDocumentValidation: false,
      caCertificate: "",
      apiBaseUrl: "http://127.0.0.1:8000",
      configSource: "db",
      kubernetesEnabled: false,
    });
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "listInstancesApiInstancesGet",
    ]);
    instancesApiMock.listInstancesApiInstancesGet.and.returnValue(of([]) as any);

    await TestBed.configureTestingModule({
      imports: [ShellComponent],
      providers: [
        provideMockStore(),
        AuthStore,
        { provide: Router, useValue: routerMock },
        { provide: MatDialog, useValue: jasmine.createSpyObj<MatDialog>("MatDialog", ["open"]) },
        { provide: AuthService, useValue: authServiceMock },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: BreakpointObserver, useValue: { observe: () => of({ matches: true }) } },
      ],
    }).compileComponents();
  });

  it("CloseNavOnMobile_HandsetLayout_DoesCloseNavigation", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;
    component.navOpen.set(true);

    // Act
    component.closeNavOnMobile();

    // Assert
    expect(component.navOpen()).toBeFalse();
  });

  it("ToggleNav_HandsetLayout_ChangesNavigationOpenState", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act
    component.toggleNav();

    // Assert
    expect(component.navOpen()).toBeTrue();
    expect(component.navOpened()).toBeTrue();
  });

  it("NavOpened_HandsetLayoutAndNavClosed_ReturnsFalse", () => {
    // Arrange
    const fixture = TestBed.createComponent(ShellComponent);
    const component = fixture.componentInstance;

    // Act
    const opened = component.navOpened();

    // Assert
    expect(opened).toBeFalse();
  });
});
