import { MAT_DIALOG_DATA, MatDialogRef } from "@angular/material/dialog";
import { TestBed } from "@angular/core/testing";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { of, throwError } from "rxjs";
import { UsersService } from "../../../api/generated";
import { NewUserDialogComponent } from "./new-user-dialog.component";
import { selectUsers, selectUsersOidcEnabled } from "../../../state/users/users.selectors";

describe("NewUserDialogComponent", () => {
  let dialogRefMock: jasmine.SpyObj<MatDialogRef<NewUserDialogComponent>>;
  let usersApiMock: jasmine.SpyObj<UsersService>;
  let mockStore: MockStore;

  beforeEach(async () => {
    dialogRefMock = jasmine.createSpyObj<MatDialogRef<NewUserDialogComponent>>("MatDialogRef", [
      "close",
    ]);
    usersApiMock = jasmine.createSpyObj<UsersService>("UsersService", [
      "inviteUserEndpointApiAuthInvitationsPost",
    ]);

    await TestBed.configureTestingModule({
      imports: [NewUserDialogComponent],
      providers: [
        provideMockStore(),
        { provide: MatDialogRef, useValue: dialogRefMock },
        { provide: UsersService, useValue: usersApiMock },
        { provide: MAT_DIALOG_DATA, useValue: { instances: ["a"], oidcEnabled: false } },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("Constructor_OidcDisabled_SetsLocalAuthAsDefault", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component.newUser.auth).toBe("local");
  });

  it("CanSave_RequiredFieldsMissing_ReturnsFalse", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "";
    component.newUser.email = "";

    // Act
    const canSave = component.canSave;

    // Assert
    expect(canSave).toBeFalse();
  });

  it("Save_ValidUserInputProvided_ClosesDialogWithPayload", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "  Alice  ";
    component.newUser.email = "  alice@example.com  ";
    component.newUser.role = "viewer";
    component.newUser.auth = "local";
    component.newUser.creationMode = "inactive";
    component.newUser.instances = ["a"];
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({
        name: "Alice",
        email: "alice@example.com",
        role: "viewer",
        auth: "local",
        instances: ["a"],
      }),
    );
    expect(dialogRefMock.close).toHaveBeenCalled();
  });

  it("Close_MethodInvoked_CallsDialogRefClose", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;

    // Act
    component.close();

    // Assert
    expect(dialogRefMock.close).toHaveBeenCalled();
  });

  it("Save_OidcEnabled_DispatchesWithOidcAuthWithoutSubject", () => {
    // Arrange
    mockStore.overrideSelector(selectUsersOidcEnabled, true);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(jasmine.objectContaining({ auth: "oidc" }));
    expect(mockStore.dispatch).not.toHaveBeenCalledWith(
      jasmine.objectContaining({ oidcSubject: jasmine.any(String) }),
    );
  });

  it("Template_OidcEnabled_HidesPasswordFieldAndOidcSubject", () => {
    // Arrange
    mockStore.overrideSelector(selectUsersOidcEnabled, true);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(NewUserDialogComponent);

    // Act
    fixture.detectChanges();
    const native = fixture.nativeElement as HTMLElement;

    // Assert
    expect(native.querySelector("#dialog-user-password")).toBeNull();
    expect(native.querySelector("#dialog-user-oidc")).toBeNull();
  });

  it("Template_OidcDisabled_OffersInviteOrInactiveWithoutInitialPassword", () => {
    // Arrange
    mockStore.overrideSelector(selectUsersOidcEnabled, false);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(NewUserDialogComponent);

    // Act
    fixture.detectChanges();
    const native = fixture.nativeElement as HTMLElement;

    // Assert
    expect(native.querySelector("#dialog-user-password")).toBeNull();
    expect(native.textContent).not.toContain("Set initial password");
    expect(native.textContent).toContain("Invite user");
    expect(fixture.componentInstance.newUser.creationMode).toBe("invite");
    expect(native.querySelector("#dialog-user-oidc")).toBeNull();
  });

  it("Save_InvalidInput_DoesNotDispatch", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "";
    component.newUser.email = "";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
  });

  it("Save_InvalidEmail_DoesNotDispatchAndShowsEmailError", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice";
    component.newUser.role = "viewer";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
    expect(component.error).toContain("valid email");
  });

  it("Save_DuplicateEmail_DoesNotDispatchAndShowsConflictError", () => {
    // Arrange
    mockStore.overrideSelector(selectUsers, [
      {
        id: 1,
        name: "Existing",
        email: "alice@example.com",
        role: "viewer",
        isActive: true,
        auth: "local",
        instances: [],
      },
    ]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = " ALICE@example.com ";
    component.newUser.role = "viewer";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
    expect(component.error).toContain("already exists");
  });

  it("CanSave_AllFieldsFilled_ReturnsTrue", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";

    // Act + Assert
    expect(component.canSave).toBeTrue();
  });

  it("Save_SmtpInvitationDelivered_ClosesDialog", async () => {
    // Arrange
    usersApiMock.inviteUserEndpointApiAuthInvitationsPost.and.returnValue(
      of({ delivered: true, manual_link: null }) as never,
    );
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";
    component.newUser.creationMode = "invite";

    // Act
    component.save();
    await fixture.whenStable();

    // Assert
    expect(dialogRefMock.close).toHaveBeenCalled();
  });

  it("Save_ManualInvitationCreated_KeepsDialogOpenForLinkCopy", async () => {
    // Arrange
    usersApiMock.inviteUserEndpointApiAuthInvitationsPost.and.returnValue(
      of({
        delivered: false,
        manual_link: "https://example.test/activate?token=secret",
      }) as never,
    );
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";
    component.newUser.creationMode = "invite";

    // Act
    component.save();
    await fixture.whenStable();

    // Assert
    expect(dialogRefMock.close).not.toHaveBeenCalled();
    expect(component.manualLink).toContain("/activate");
  });

  it("Save_InvitationFails_KeepsDialogOpenAndShowsError", async () => {
    // Arrange
    usersApiMock.inviteUserEndpointApiAuthInvitationsPost.and.returnValue(
      throwError(() => ({ error: { detail: "SMTP delivery failed." } })),
    );
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";
    component.newUser.creationMode = "invite";

    // Act
    component.save();
    await fixture.whenStable();

    // Assert
    expect(dialogRefMock.close).not.toHaveBeenCalled();
    expect(component.error).toBe("SMTP delivery failed.");
  });
});
