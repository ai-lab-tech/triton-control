import { TestBed } from "@angular/core/testing";
import { ActivatedRoute } from "@angular/router";
import { of, throwError } from "rxjs";

import { UsersService } from "../../api/generated";
import { AccountLifecyclePageComponent } from "./account-lifecycle-page.component";

describe("AccountLifecyclePageComponent", () => {
  function setup(
    mode: "forgot" | "activate" | "reset",
    token = "",
    overrides: Partial<jasmine.SpyObj<UsersService>> = {},
  ): AccountLifecyclePageComponent {
    const api = jasmine.createSpyObj<UsersService>("UsersService", [
      "inspectInvitationEndpointApiAuthInvitationsInspectPost",
      "inspectResetEndpointApiAuthPasswordResetsInspectPost",
      "activateInvitationEndpointApiAuthInvitationsActivatePost",
      "completeResetEndpointApiAuthPasswordResetsCompletePost",
      "forgotPasswordEndpointApiAuthForgotPasswordPost",
    ]);
    Object.assign(api, overrides);
    TestBed.configureTestingModule({
      imports: [AccountLifecyclePageComponent],
      providers: [
        { provide: UsersService, useValue: api },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              data: { mode },
              queryParamMap: { get: () => token || null },
            },
          },
        },
      ],
    });
    return TestBed.createComponent(AccountLifecyclePageComponent).componentInstance;
  }

  afterEach(() => TestBed.resetTestingModule());

  it("ForgotPassword_AnyAcceptedAccount_ShowsNeutralConfirmation", async () => {
    const component = setup("forgot", "", {
      forgotPasswordEndpointApiAuthForgotPasswordPost: jasmine
        .createSpy()
        .and.returnValue(of({ message: "neutral" })),
    });
    component.email = "person@example.test";

    await component.requestReset();

    expect(component.completed).toBeTrue();
    expect(component.message).toContain("If the account is eligible");
  });

  it("Invitation_InvalidToken_ShowsSafeMessage", async () => {
    const component = setup("activate", "invalid", {
      inspectInvitationEndpointApiAuthInvitationsInspectPost: jasmine
        .createSpy()
        .and.returnValue(of({ valid: false })),
    });

    await component.ngOnInit();

    expect(component.validLink).toBeFalse();
    expect(component.error).toBe("This link is invalid or expired.");
  });

  it("Reset_ValidTokenAndPassword_CompletesAndClearsSecrets", async () => {
    const component = setup("reset", "bearer-token", {
      inspectResetEndpointApiAuthPasswordResetsInspectPost: jasmine
        .createSpy()
        .and.returnValue(of({ valid: true, email: "person@example.test" })),
      completeResetEndpointApiAuthPasswordResetsCompletePost: jasmine
        .createSpy()
        .and.returnValue(of({ reset: true })),
    });
    await component.ngOnInit();
    component.password = "Validpass123!";
    component.confirmPassword = "Validpass123!";

    await component.submitPassword();

    expect(component.completed).toBeTrue();
    expect(component.token).toBe("");
    expect(component.password).toBe("");
  });

  it("ForgotPassword_Unavailable_ShowsAvailabilityError", async () => {
    const component = setup("forgot", "", {
      forgotPasswordEndpointApiAuthForgotPasswordPost: jasmine
        .createSpy()
        .and.returnValue(throwError(() => new Error("disabled"))),
    });
    component.email = "person@example.test";

    await component.requestReset();

    expect(component.completed).toBeFalse();
    expect(component.error).toContain("unavailable");
  });
});
