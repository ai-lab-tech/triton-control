/* eslint-disable @typescript-eslint/no-explicit-any */
import { HttpClient } from "@angular/common/http";
import { TestBed } from "@angular/core/testing";
import { MatDialog } from "@angular/material/dialog";
import { of, Subject } from "rxjs";
import { Action } from "@ngrx/store";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { provideMockActions } from "@ngrx/effects/testing";
import { InstancesService, UsersService } from "../../api/generated/index";
import { UsersPageComponent } from "./users-page.component";
import { selectUsersInstances } from "../../state/users/users.selectors";
import {
  addInstanceToUserRequested,
  deleteUserRequested,
  removeInstanceFromUserRequested,
  updateUserRoleFailed,
  updateUserRoleRequested,
  updateUserRoleSucceeded,
} from "../../state/users/users.actions";

describe("UsersPageComponent", () => {
  let usersApiMock: jasmine.SpyObj<UsersService>;
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let dialogMock: jasmine.SpyObj<MatDialog>;
  let mockStore: MockStore;
  let actionsSubject: Subject<Action>;

  beforeEach(async () => {
    usersApiMock = jasmine.createSpyObj<UsersService>("UsersService", [
      "listUsersApiAuthUsersGet",
      "authOptionsEndpointApiAuthOptionsGet",
      "registerUserEndpointApiAuthRegisterPost",
      "updateUserInstancesApiAuthUsersUserIdInstancesPut",
      "deleteUserApiAuthUsersUserIdDelete",
      "updateUserRoleApiAuthUsersUserIdRolePut",
    ]);
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "listInstancesApiInstancesGet",
    ]);
    dialogMock = jasmine.createSpyObj<MatDialog>("MatDialog", ["open"]);
    actionsSubject = new Subject<Action>();

    usersApiMock.authOptionsEndpointApiAuthOptionsGet.and.returnValue(
      of({ oidc_enabled: true } as any),
    );
    usersApiMock.listUsersApiAuthUsersGet.and.returnValue(of([] as any));
    instancesApiMock.listInstancesApiInstancesGet.and.returnValue(of([] as any));
    dialogMock.open.and.returnValue({ afterClosed: () => of(undefined) } as any);

    await TestBed.configureTestingModule({
      imports: [UsersPageComponent],
      providers: [
        provideMockStore(),
        provideMockActions(() => actionsSubject),
        { provide: HttpClient, useValue: {} },
        { provide: UsersService, useValue: usersApiMock },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: MatDialog, useValue: dialogMock },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("AvailableInstances_UserHasAssignedSubset_ReturnsOnlyUnassignedInstances", () => {
    // Arrange
    mockStore.overrideSelector(selectUsersInstances, ["node-1", "node-2", "node-3"]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    const user = {
      id: 1,
      name: "A",
      email: "a@example.com",
      role: "viewer",
      isActive: true,
      auth: "local" as const,
      instances: ["node-2"],
    };

    // Act
    const available = component.availableInstances(user);

    // Assert
    expect(available).toEqual(["node-1", "node-3"]);
  });

  it("AddInstanceToUser_NoSelectionConfigured_DoesNotDispatchAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 1,
      name: "A",
      email: "a@example.com",
      role: "viewer",
      isActive: true,
      auth: "local" as const,
      instances: ["node-1"],
    };

    // Act
    component.addInstanceToUser(user);

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalledWith(
      jasmine.objectContaining({ type: addInstanceToUserRequested.type }),
    );
  });

  it("AddInstanceToUser_SelectionPresent_DispatchesActionAndClearsPendingSelection", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 1,
      name: "A",
      email: "a@example.com",
      role: "viewer",
      isActive: true,
      auth: "local" as const,
      instances: ["node-1"],
    };
    component.pendingInstanceByEmail[user.email] = "node-2";

    // Act
    component.addInstanceToUser(user);

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      addInstanceToUserRequested({ userId: 1, instances: ["node-1", "node-2"] }),
    );
    expect(component.pendingInstanceByEmail[user.email]).toBe("");
  });

  it("RemoveInstanceFromUser_InstanceExists_DispatchesRemoveAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 2,
      name: "B",
      email: "b@example.com",
      role: "member",
      isActive: true,
      auth: "local" as const,
      instances: ["node-1", "node-2"],
    };

    // Act
    component.removeInstanceFromUser(user, "node-1");

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      removeInstanceFromUserRequested({ userId: 2, instances: ["node-2"] }),
    );
  });

  it("DeleteUser_UserExists_DispatchesDeleteUserAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 3,
      name: "C",
      email: "c@example.com",
      role: "viewer",
      isActive: true,
      auth: "local" as const,
      instances: [],
    };

    // Act
    component.deleteUser(user);

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      deleteUserRequested({ userId: 3, email: "c@example.com" }),
    );
  });

  it("UpdateRole_EmptyRoleProvided_DoesNotDispatchAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 4,
      name: "D",
      email: "d@example.com",
      role: "viewer",
      isActive: false,
      auth: "local" as const,
      instances: [],
    };

    // Act
    component.updateRole(user, "");

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
  });

  it("UpdateRole_SameRoleAndAlreadyActive_SkipsDispatch", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 5,
      name: "E",
      email: "e@example.com",
      role: "admin",
      isActive: true,
      auth: "local" as const,
      instances: [],
    };

    // Act
    component.updateRole(user, "admin");

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
  });

  it("UpdateRole_ValidRoleProvided_DispatchesUpdateRoleAction", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");
    const user = {
      id: 6,
      name: "F",
      email: "f@example.com",
      role: "viewer",
      isActive: false,
      auth: "local" as const,
      instances: [],
    };

    // Act
    component.updateRole(user, "member");

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      updateUserRoleRequested({ userId: 6, role: "member", prevRole: "viewer" }),
    );
  });

  it("UpdateRole_ActionFailed_RestoresPendingRoleViaActionSubscription", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    component.pendingRoleByUserId[7] = "member";

    // Act — emit the failed action through the actions stream
    actionsSubject.next(updateUserRoleFailed({ userId: 7, prevRole: "viewer", message: "error" }));

    // Assert — constructor subscription restores the pending role to prevRole
    expect(component.pendingRoleByUserId[7]).toBe("viewer");
  });

  it("UpdateRole_ActionSucceeded_UpdatesPendingRoleViaActionSubscription", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;

    // Act — emit the succeeded action through the actions stream
    actionsSubject.next(updateUserRoleSucceeded({ userId: 8, role: "admin" }));

    // Assert — constructor subscription sets the pending role
    expect(component.pendingRoleByUserId[8]).toBe("admin");
  });

  it("ApproveUser_PendingRoleAvailable_DelegatesToUpdateRoleWithPendingRole", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    const user = {
      id: 7,
      name: "G",
      email: "g@example.com",
      role: "viewer",
      isActive: false,
      auth: "local" as const,
      instances: [],
    };
    component.pendingRoleByUserId[user.id] = "admin";
    const updateRoleSpy = spyOn(component, "updateRole");

    // Act
    component.approveUser(user);

    // Assert
    expect(updateRoleSpy).toHaveBeenCalledWith(user, "admin");
  });

  it("OpenNewUserDialog_Called_OpensDialog", () => {
    // Arrange
    const fixture = TestBed.createComponent(UsersPageComponent);
    const component = fixture.componentInstance;
    (component as any).dialog = dialogMock;

    // Act
    component.openNewUserDialog();

    // Assert
    expect(dialogMock.open).toHaveBeenCalled();
  });
});
