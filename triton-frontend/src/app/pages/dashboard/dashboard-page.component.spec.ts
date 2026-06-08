/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { AuthStore } from "../../shared/auth/auth.store";
import { AuthService } from "../../shared/auth/auth.service";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { DashboardPageComponent } from "./dashboard-page.component";
import {
  selectDashboardInstances,
  selectDashboardStats,
} from "../../state/dashboard/dashboard.selectors";

describe("DashboardPageComponent", () => {
  let authServiceMock: jasmine.SpyObj<AuthService>;
  let authState: InstanceType<typeof AuthStore>;
  let mockStore: MockStore;

  beforeEach(async () => {
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", ["login"]);

    await TestBed.configureTestingModule({
      imports: [DashboardPageComponent],
      providers: [
        provideMockStore(),
        AuthStore,
        { provide: AuthService, useValue: authServiceMock },
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
    const fixture = TestBed.createComponent(DashboardPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("Login_MethodInvoked_CallsAuthLogin", () => {
    // Arrange
    const fixture = TestBed.createComponent(DashboardPageComponent);
    const component = fixture.componentInstance;

    // Act
    component.login();

    // Assert
    expect(authServiceMock.login).toHaveBeenCalled();
  });

  it("LoadInstances_UserLoggedInAndApisSucceed_MapsRowsAndStats", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardInstances, [
      { id: "1", name: "node-1", url: "http://n1" } as any,
    ]);
    mockStore.overrideSelector(selectDashboardStats, [
      { label: "Configured Instances", value: 1 } as any,
    ]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(DashboardPageComponent);
    const component = fixture.componentInstance;
    authState.setAuthenticatedUser({ name: "Admin", role: "admin", accessAllowed: true });

    // Assert
    expect(component.instances().length).toBe(1);
    expect(component.stats().find((s: any) => s.label === "Configured Instances")?.value).toBe(1);
  });

  it("LoadInstances_ApiFails_ResetsRowsAndStatsToLoading", () => {
    // Arrange
    mockStore.overrideSelector(selectDashboardInstances, []);
    mockStore.overrideSelector(selectDashboardStats, [
      { label: "Configured Instances", value: "Loading..." } as any,
    ]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(DashboardPageComponent);
    const component = fixture.componentInstance as any;
    authState.setAuthenticatedUser({ name: "Admin", role: "admin", accessAllowed: true });

    // Assert
    expect(component.instances()).toEqual([]);
    expect(component.stats().find((s: any) => s.label === "Configured Instances")?.value).toBe(
      "Loading...",
    );
  });
});
