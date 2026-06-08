import { MAT_DIALOG_DATA, MatDialogRef } from "@angular/material/dialog";
import { TestBed } from "@angular/core/testing";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { NewUserDialogComponent } from "./new-user-dialog.component";
import { selectUsers, selectUsersOidcEnabled } from "../../../state/users/users.selectors";

describe("NewUserDialogComponent", () => {
  let dialogRefMock: jasmine.SpyObj<MatDialogRef<NewUserDialogComponent>>;
  let mockStore: MockStore;

  beforeEach(async () => {
    dialogRefMock = jasmine.createSpyObj<MatDialogRef<NewUserDialogComponent>>("MatDialogRef", [
      "close",
    ]);

    await TestBed.configureTestingModule({
      imports: [NewUserDialogComponent],
      providers: [
        provideMockStore(),
        { provide: MatDialogRef, useValue: dialogRefMock },
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
    component.newUser.password = "Validpass123!";
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

  it("Template_OidcDisabled_ShowsPasswordFieldAndHidesOidcSubject", () => {
    // Arrange
    mockStore.overrideSelector(selectUsersOidcEnabled, false);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(NewUserDialogComponent);

    // Act
    fixture.detectChanges();
    const native = fixture.nativeElement as HTMLElement;

    // Assert
    expect(native.querySelector("#dialog-user-password")).not.toBeNull();
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

  it("Save_LocalPasswordTooShort_DoesNotDispatchAndShowsPolicyError", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewUserDialogComponent);
    const component = fixture.componentInstance;
    component.newUser.name = "Alice";
    component.newUser.email = "alice@example.com";
    component.newUser.role = "viewer";
    component.newUser.password = "short";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).not.toHaveBeenCalled();
    expect(component.error).toContain("12-128");
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
});
