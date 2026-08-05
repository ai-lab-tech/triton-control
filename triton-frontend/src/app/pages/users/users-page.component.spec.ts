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
import { selectUsers, selectUsersInstances } from "../../state/users/users.selectors";
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
      "getEmailSettingsEndpointApiAuthEmailSettingsGet",
      "registerUserEndpointApiAuthRegisterPost",
      "updateUserInstancesApiAuthUsersUserIdInstancesPut",
      "deleteUserApiAuthUsersUserIdDelete",
      "updateUserRoleApiAuthUsersUserIdRolePut",
      "adminResetEndpointApiAuthPasswordResetsPost",
      "reissueInvitationEndpointApiAuthInvitationsUserIdReissuePost",
      "revokeInvitationEndpointApiAuthInvitationsUserIdDelete",
    ]);
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "listInstancesApiInstancesGet",
    ]);
    dialogMock = jasmine.createSpyObj<MatDialog>("MatDialog", ["open"]);
    actionsSubject = new Subject<Action>();

    usersApiMock.authOptionsEndpointApiAuthOptionsGet.and.returnValue(
      of({ oidc_enabled: true } as any),
    );
    usersApiMock.getEmailSettingsEndpointApiAuthEmailSettingsGet.and.returnValue(
      of({ delivery_mode: "disabled" } as any),
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
    mockStore.overrideSelector(selectUsersInstances, []);
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

  it("InvitationPending_ShowsInvitationActionsWithoutApprove", async () => {
    // Arrange
    mockStore.overrideSelector(selectUsers, [
      {
        id: 8,
        name: "Invited User",
        email: "invited@example.com",
        role: "viewer",
        isActive: false,
        accountStatus: "invitation_pending",
        auth: "local",
        instances: [],
      },
    ]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(UsersPageComponent);

    // Act
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const native = fixture.nativeElement as HTMLElement;
    const actionButtons = Array.from(
      native.querySelectorAll<HTMLButtonElement>(".actions-cell button"),
    );
    const actions = actionButtons.map((button) => button.textContent?.trim());
    const reissueButton = native.querySelector<HTMLButtonElement>(
      '[data-testid="reissue-invitation-action"]',
    );
    const cancelButton = native.querySelector<HTMLButtonElement>(
      '[data-testid="cancel-invitation-action"]',
    );

    // Assert
    expect(actions).toContain("Reissue invite");
    expect(actions).toContain("Cancel invitation");
    expect(actions).not.toContain("Approve");
    expect(actions.some((action) => action?.includes("Delete"))).toBeTrue();
    expect(reissueButton?.disabled).toBeTrue();
    expect(cancelButton?.disabled).toBeTrue();
    expect(reissueButton?.title).toContain("Enable manual-link or SMTP");
    expect(cancelButton?.title).toContain("Enable manual-link or SMTP");

    reissueButton?.click();
    cancelButton?.click();
    expect(
      usersApiMock.reissueInvitationEndpointApiAuthInvitationsUserIdReissuePost,
    ).not.toHaveBeenCalled();
    expect(
      usersApiMock.revokeInvitationEndpointApiAuthInvitationsUserIdDelete,
    ).not.toHaveBeenCalled();
  });

  it("ResetPassword_EmailLifecycleDisabled_DisablesActionWithExplanation", async () => {
    // Arrange
    mockStore.overrideSelector(selectUsers, [
      {
        id: 9,
        name: "Local User",
        email: "local@example.com",
        role: "viewer",
        isActive: true,
        auth: "local",
        instances: [],
      },
    ]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(UsersPageComponent);

    // Act
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const button = fixture.nativeElement.querySelector(
      '[data-testid="reset-password-action"]',
    ) as HTMLButtonElement;

    // Assert
    expect(button.disabled).toBeTrue();
    expect(button.title).toContain("Enable manual-link or SMTP");
    button.click();
    expect(usersApiMock.adminResetEndpointApiAuthPasswordResetsPost).not.toHaveBeenCalled();
  });

  for (const deliveryMode of ["manual-link", "smtp"]) {
    it(`ResetPassword_EmailLifecycle${deliveryMode}_EnablesAction`, async () => {
      // Arrange
      usersApiMock.getEmailSettingsEndpointApiAuthEmailSettingsGet.and.returnValue(
        of({ delivery_mode: deliveryMode } as any),
      );
      mockStore.overrideSelector(selectUsers, [
        {
          id: 10,
          name: "Local User",
          email: "local@example.com",
          role: "viewer",
          isActive: true,
          auth: "local",
          instances: [],
        },
      ]);
      mockStore.refreshState();
      const fixture = TestBed.createComponent(UsersPageComponent);

      // Act
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const button = fixture.nativeElement.querySelector(
        '[data-testid="reset-password-action"]',
      ) as HTMLButtonElement;

      // Assert
      expect(button.disabled).toBeFalse();
      expect(button.title).toBe("");
    });
  }

  it("ResetPassword_SmtpDeliveryPending_ShowsProgressAndPreventsDuplicates", async () => {
    // Arrange
    const response$ = new Subject<any>();
    usersApiMock.getEmailSettingsEndpointApiAuthEmailSettingsGet.and.returnValue(
      of({ delivery_mode: "smtp" } as any),
    );
    usersApiMock.adminResetEndpointApiAuthPasswordResetsPost.and.returnValue(response$);
    const user = {
      id: 12,
      name: "Local User",
      email: "local@example.com",
      role: "viewer",
      isActive: true,
      auth: "local" as const,
      instances: [],
    };
    mockStore.overrideSelector(selectUsers, [user]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(UsersPageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    // Act
    const request = fixture.componentInstance.issuePasswordReset(user);
    void fixture.componentInstance.issuePasswordReset(user);
    fixture.detectChanges();
    const native = fixture.nativeElement as HTMLElement;
    const button = native.querySelector<HTMLButtonElement>('[data-testid="reset-password-action"]');

    // Assert
    expect(usersApiMock.adminResetEndpointApiAuthPasswordResetsPost).toHaveBeenCalledTimes(1);
    expect(fixture.componentInstance.passwordResetUserId()).toBe(user.id);
    expect(button?.disabled).toBeTrue();
    expect(button?.getAttribute("aria-busy")).toBe("true");
    expect(button?.querySelector("mat-spinner")).not.toBeNull();
    expect(button?.textContent).toContain("Sending reset");
    expect(native.querySelector('[role="status"]')?.textContent).toContain("few seconds");

    // Act
    response$.next({ delivered: true, manual_link: null });
    response$.complete();
    await request;
    fixture.detectChanges();

    // Assert
    expect(fixture.componentInstance.passwordResetUserId()).toBeNull();
    expect(button?.querySelector("mat-spinner")).toBeNull();
    expect(fixture.componentInstance.lifecycleMessage()).toContain("SMTP server");
  });

  it("SendInvitation_ManualLinkResponse_RendersImmediatelyAndPreventsDuplicateRequests", async () => {
    // Arrange
    const response$ = new Subject<any>();
    usersApiMock.getEmailSettingsEndpointApiAuthEmailSettingsGet.and.returnValue(
      of({ delivery_mode: "manual-link" } as any),
    );
    usersApiMock.reissueInvitationEndpointApiAuthInvitationsUserIdReissuePost.and.returnValue(
      response$,
    );
    const user = {
      id: 11,
      name: "Inactive User",
      email: "inactive@example.com",
      role: "viewer",
      isActive: false,
      accountStatus: "inactive" as const,
      auth: "local" as const,
      instances: [],
    };
    mockStore.overrideSelector(selectUsers, [user]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(UsersPageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const button = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>(
        ".actions-cell button",
      ),
    ).find((candidate) => candidate.textContent?.includes("Send invitation"));
    expect(button).toBeDefined();
    expect(fixture.componentInstance.emailLifecycleAvailable()).toBeTrue();
    expect(button!.disabled).toBeFalse();

    // Act
    const request = fixture.componentInstance.reissueInvitation(user);
    void fixture.componentInstance.reissueInvitation(user);
    fixture.detectChanges();

    // Assert
    expect(
      usersApiMock.reissueInvitationEndpointApiAuthInvitationsUserIdReissuePost,
    ).toHaveBeenCalledTimes(1);
    expect(button!.disabled).toBeTrue();
    expect(button!.textContent).toContain("Creating link...");

    // Act
    response$.next({
      delivered: false,
      manual_link: "https://example.test/activate-account?token=latest",
    });
    response$.complete();
    await request;
    fixture.detectChanges();

    // Assert
    const linkInput = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      'input[aria-label="One-time account link"]',
    );
    expect(linkInput?.value).toContain("token=latest");
    expect(fixture.componentInstance.lifecycleActionRunning()).toBeFalse();
  });
});
