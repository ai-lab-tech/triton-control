/* eslint-disable @typescript-eslint/no-explicit-any */
import { ComponentFixture, TestBed } from "@angular/core/testing";
import { By } from "@angular/platform-browser";
import { MatTabGroup } from "@angular/material/tabs";
import { NoopAnimationsModule } from "@angular/platform-browser/animations";
import { provideStore, provideState } from "@ngrx/store";
import { provideEffects } from "@ngrx/effects";
import { SettingsPageComponent } from "./settings-page.component";
import { AuthService } from "../../shared/auth/auth.service";
import { SettingsEffects } from "../../state/settings/settings.effects";
import { SETTINGS_FEATURE_KEY, settingsReducer } from "../../state/settings/settings.reducer";
import { UsersService } from "../../api/generated";
import { of, Subject, throwError } from "rxjs";

describe("SettingsPageComponent", () => {
  let emailSettingsResponse: Record<string, unknown>;
  let getEmailSettingsSpy: jasmine.Spy;
  let updateEmailSettingsSpy: jasmine.Spy;
  let testEmailSpy: jasmine.Spy;
  let authServiceMock: {
    getOidcSettings: jasmine.Spy;
    saveOidcSettings: jasmine.Spy;
  };

  async function openEmailTab(fixture: ComponentFixture<SettingsPageComponent>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    const tabGroup = fixture.debugElement.query(By.directive(MatTabGroup))
      .componentInstance as MatTabGroup;
    tabGroup.selectedIndex = 1;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  beforeEach(() => {
    emailSettingsResponse = {
      config_source: "env",
      delivery_mode: "disabled",
      read_only: true,
    };
    getEmailSettingsSpy = jasmine
      .createSpy("getEmailSettings")
      .and.callFake(() => of(emailSettingsResponse));
    updateEmailSettingsSpy = jasmine
      .createSpy("updateEmailSettings")
      .and.callFake((request: Record<string, unknown>) =>
        of({
          config_source: "db",
          ...request,
          smtp_password_configured: !!request["smtp_password"],
          read_only: false,
        }),
      );
    testEmailSpy = jasmine
      .createSpy("testEmail")
      .and.returnValue(of({ message: "SMTP server accepted the test message." }));
    authServiceMock = {
      getOidcSettings: jasmine.createSpy("getOidcSettings").and.resolveTo({
        oidcEnabled: true,
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
      }),
      saveOidcSettings: jasmine
        .createSpy("saveOidcSettings")
        .and.callFake(async (settings: any) => settings),
    };
  });

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SettingsPageComponent, NoopAnimationsModule],
      providers: [
        provideStore(),
        provideState(SETTINGS_FEATURE_KEY, settingsReducer),
        provideEffects([SettingsEffects]),
        { provide: AuthService, useValue: authServiceMock },
        {
          provide: UsersService,
          useValue: {
            getEmailSettingsEndpointApiAuthEmailSettingsGet: getEmailSettingsSpy,
            updateEmailSettingsEndpointApiAuthEmailSettingsPut: updateEmailSettingsSpy,
            testEmailEndpointApiAuthEmailSettingsTestPost: testEmailSpy,
          },
        },
      ],
    }).compileComponents();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("ComponentInitialized_DefaultSettingsLoaded_OidcEnabledIsTrue", () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component.oidcEnabled).toBeTrue();
  });

  it("SettingsNavigation_Rendered_ShowsOidcAndEmailTabs", async () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);

    // Act
    fixture.detectChanges();
    await fixture.whenStable();
    const tabLabels = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[role="tab"]'),
    ).map((tab) => tab.textContent?.trim());

    // Assert
    expect(tabLabels).toEqual(["OIDC", "Email"]);
  });

  it("SaveSettings_SaveSucceeds_ShowsSuccessMessage", async () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();
    component.saveSettings();
    await fixture.whenStable();

    // Assert
    expect(authServiceMock.saveOidcSettings).toHaveBeenCalled();
    expect(component.message()).toBe("Settings saved.");
    expect(component.messageTone()).toBe("success");
  });

  it("LoadSettings_ApiFails_ShowsFallbackErrorMessage", async () => {
    // Arrange
    authServiceMock.getOidcSettings.and.rejectWith(new Error(""));
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();

    // Assert
    expect(component.message()).toBe("Failed to load settings.");
    expect(component.messageTone()).toBe("error");
  });

  it("SaveSettings_PreflightRedirectResponse_ShowsRedirectInfoMessage", async () => {
    // Arrange
    authServiceMock.saveOidcSettings.and.resolveTo(null);
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();
    component.saveSettings();
    await fixture.whenStable();

    // Assert
    expect(component.message()).toBe("Redirecting to OIDC login...");
    expect(component.messageTone()).toBe("info");
  });

  it("SaveSettings_ApiRejectsWithMessage_ShowsProvidedErrorMessage", async () => {
    // Arrange
    authServiceMock.saveOidcSettings.and.rejectWith(new Error("Broken configuration"));
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();
    component.saveSettings();
    await fixture.whenStable();

    // Assert
    expect(component.message()).toBe("Broken configuration");
    expect(component.messageTone()).toBe("error");
  });

  it("ClientSecretPlaceholder_StoredSecretConfigured_ShowsStoredSecretText", async () => {
    // Arrange
    authServiceMock.getOidcSettings.and.resolveTo({
      oidcEnabled: true,
      issuer: "",
      clientId: "",
      clientSecret: "",
      clientSecretConfigured: true,
      redirectUri: "",
      scopes: "openid profile email",
      strictDiscoveryDocumentValidation: false,
      caCertificate: "",
      apiBaseUrl: "http://127.0.0.1:8000",
      configSource: "db",
      kubernetesEnabled: false,
    });
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();

    // Assert
    expect(component.settings.clientSecret).toBe("");
    expect(component.getClientSecretPlaceholder()).toBe("Stored secret configured");
  });

  it("OidcConfigSourceEnv_SettingsAreReadOnly_DisablesOidcControls", async () => {
    // Arrange
    authServiceMock.getOidcSettings.and.resolveTo({
      oidcEnabled: false,
      issuer: "https://issuer.example",
      clientId: "client-id",
      clientSecret: "",
      clientSecretConfigured: true,
      redirectUri: "http://localhost/callback",
      scopes: "openid profile email",
      strictDiscoveryDocumentValidation: true,
      caCertificate: "",
      apiBaseUrl: "http://127.0.0.1:8000",
      configSource: "env",
      kubernetesEnabled: false,
    });
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;

    // Act
    await fixture.whenStable();
    fixture.detectChanges();
    component.oidcEnabled = true;
    component.saveSettings();
    fixture.detectChanges();

    // Assert
    const native = fixture.nativeElement as HTMLElement;
    expect(component.canEditOidcSettings()).toBeFalse();
    expect(component.canSaveSettings()).toBeFalse();
    expect(native.querySelector<HTMLInputElement>("#oidc-enabled")?.disabled).toBeTrue();
    expect(native.querySelector<HTMLInputElement>("#oidc-issuer")?.readOnly).toBeTrue();
    expect(native.textContent).toContain("Managed by OIDC_CONFIG_SOURCE=env.");
    expect(authServiceMock.saveOidcSettings).not.toHaveBeenCalled();
  });

  it("EmailDeliveryDisabled_DbManaged_DisablesDependentConfiguration", async () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.emailSettings.configSource = "db";
    component.emailSettings.deliveryMode = "disabled";
    component.emailAdvancedExpanded = true;
    component.emailTemplatesExpanded = true;

    // Act
    await openEmailTab(fixture);
    const native = fixture.nativeElement as HTMLElement;

    // Assert
    expect(component.canEditEmailSettings()).toBeTrue();
    expect(component.canEditEmailLifecycleConfiguration()).toBeFalse();
    expect(native.querySelector<HTMLInputElement>("#invite-expiry")?.disabled).toBeTrue();
    expect(native.querySelector<HTMLInputElement>("#reset-expiry")?.disabled).toBeTrue();
    expect(native.querySelector<HTMLInputElement>("#invite-subject")?.disabled).toBeTrue();
    expect(native.querySelector<HTMLTextAreaElement>("#invite-html-template")?.disabled).toBeTrue();
    expect(native.textContent).toContain(
      "Enable manual links or SMTP to configure account email lifecycle settings.",
    );
  });

  it("EmailDeliveryEnabled_DbManaged_EnablesDependentConfiguration", async () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.emailSettings.configSource = "db";
    component.emailSettings.deliveryMode = "manual-link";
    component.emailAdvancedExpanded = true;
    component.emailTemplatesExpanded = true;

    // Act
    await openEmailTab(fixture);
    const native = fixture.nativeElement as HTMLElement;

    // Assert
    expect(component.canEditEmailLifecycleConfiguration()).toBeTrue();
    expect(native.querySelector<HTMLInputElement>("#email-public-url")?.disabled).toBeFalse();
    expect(native.querySelector<HTMLInputElement>("#invite-expiry")?.disabled).toBeFalse();
    expect(native.querySelector<HTMLInputElement>("#reset-expiry")?.disabled).toBeFalse();
    expect(native.querySelector<HTMLInputElement>("#invite-subject")?.disabled).toBeFalse();
    expect(
      native.querySelector<HTMLTextAreaElement>("#invite-html-template")?.disabled,
    ).toBeFalse();
  });

  it("EmailAdvancedSettings_InitiallyCollapsed_ExpandsOnDemand", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    await openEmailTab(fixture);
    const native = fixture.nativeElement as HTMLElement;
    const advanced = native.querySelector<HTMLDetailsElement>("details.email-collapsible");
    const wasInitiallyOpen = advanced?.open;
    const fieldsWereInitiallyRendered =
      native.querySelector("#smtp-ca") !== null ||
      native.querySelector("#invite-expiry") !== null ||
      native.querySelector("#reset-expiry") !== null;

    // Act
    if (advanced) {
      advanced.open = true;
      advanced.dispatchEvent(new Event("toggle"));
    }
    fixture.detectChanges();

    // Assert
    expect(advanced).not.toBeNull();
    expect(wasInitiallyOpen).toBeFalse();
    expect(fieldsWereInitiallyRendered).toBeFalse();
    expect(advanced?.open).toBeTrue();
    expect(native.querySelector("#smtp-ca")).not.toBeNull();
    expect(native.querySelector("#invite-expiry")).not.toBeNull();
    expect(native.querySelector("#reset-expiry")).not.toBeNull();
    expect(advanced?.textContent).toContain("Security and link expiry");
    expect(advanced?.textContent).toContain("1440 minutes equals 24 hours.");
  });

  it("EmailOptionalPanels_UseClearConsistentDescriptions", async () => {
    // Arrange
    const fixture = TestBed.createComponent(SettingsPageComponent);

    // Act
    await openEmailTab(fixture);
    const native = fixture.nativeElement as HTMLElement;
    const panels = native.querySelectorAll<HTMLDetailsElement>("details.email-collapsible");

    // Assert
    expect(panels.length).toBe(2);
    expect(panels[0].textContent).toContain("Security and link expiry");
    expect(panels[0].textContent).toContain("invitation and password reset links remain valid");
    expect(panels[1].textContent).toContain("Email content");
    expect(panels[1].textContent).toContain("invitations and password resets");
  });

  it("TestEmail_DbManagedUnsavedSmtpSettings_DisablesTestUntilSaved", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "disabled",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.emailSettings.deliveryMode = "smtp";
    component.testRecipient = "admin@example.com";

    // Act
    await openEmailTab(fixture);
    await component.sendTestEmail();
    const native = fixture.nativeElement as HTMLElement;
    const sendButton = Array.from(native.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Send test email"),
    );

    // Assert
    expect(component.emailSettingsHaveUnsavedChanges()).toBeTrue();
    expect(component.canSendTestEmail()).toBeFalse();
    expect(sendButton?.disabled).toBeTrue();
    expect(native.textContent).toContain("Save SMTP settings before sending a test email.");
    expect(testEmailSpy).not.toHaveBeenCalled();
  });

  it("TestEmail_DbManagedSavedSmtpSettings_EnablesTest", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.testRecipient = "admin@example.com";

    // Act
    fixture.detectChanges();

    // Assert
    expect(component.emailSettingsHaveUnsavedChanges()).toBeFalse();
    expect(component.canSendTestEmail()).toBeTrue();
  });

  it("TestEmail_SmtpServerDisconnects_ShowsActionableBackendMessage", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    testEmailSpy.and.returnValue(
      throwError(() => ({
        error: {
          detail:
            "SMTP delivery failed (SMTPServerDisconnected): verify the SMTP endpoint, port, and TLS mode.",
        },
      })),
    );
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await openEmailTab(fixture);
    component.testRecipient = "admin@example.com";

    // Act
    await component.sendTestEmail();
    fixture.detectChanges();

    // Assert
    expect(component.emailMessageTone()).toBe("error");
    expect(component.emailMessage()).toContain("SMTPServerDisconnected");
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]')?.textContent,
    ).toContain("verify the SMTP endpoint");
  });

  it("TestEmail_RequestPending_ShowsTestingStateWithoutShowingSavingState", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const pendingTest = new Subject<{ message?: string }>();
    testEmailSpy.and.returnValue(pendingTest);
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await openEmailTab(fixture);
    component.testRecipient = "admin@example.com";

    // Act
    const request = component.sendTestEmail();
    fixture.detectChanges();
    const buttons = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll("button"),
    ).map((button) => button.textContent?.trim());

    // Assert
    expect(component.emailTesting()).toBeTrue();
    expect(component.emailSaving()).toBeFalse();
    expect(buttons).toContain("Sending test…");
    expect(buttons).toContain("Save email settings");
    expect(buttons).not.toContain("Saving…");

    pendingTest.next({ message: "SMTP server accepted the test message." });
    pendingTest.complete();
    await request;
  });

  it("LoadEmailSettings_Port465WithStartTls_CorrectsToImplicitTlsBeforeSaving", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      smtp_host: "smtp.example.test",
      smtp_port: 465,
      smtp_tls_mode: "starttls",
      sender_email: "noreply@example.test",
      public_app_url: "https://control.example.test",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();

    // Act
    await component.saveEmailSettings();

    // Assert
    expect(component.emailSettings.smtpTlsMode).toBe("tls");
    expect(updateEmailSettingsSpy).toHaveBeenCalledWith(
      jasmine.objectContaining({
        smtp_port: 465,
        smtp_tls_mode: "tls",
      }),
    );
  });

  it("LoadEmailSettings_BlankSubjects_AppliesAndSavesBuiltInDefaults", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      public_app_url: "https://control.example.test",
      smtp_host: "smtp.example.test",
      sender_email: "noreply@example.test",
      invite_subject: "",
      reset_subject: "   ",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    expect(component.emailSettingsHaveUnsavedChanges()).toBeTrue();

    // Act
    await component.saveEmailSettings();

    // Assert
    expect(component.emailSettings.inviteSubject).toBe("You are invited to Triton Control");
    expect(component.emailSettings.resetSubject).toBe("Reset your Triton Control password");
    expect(updateEmailSettingsSpy).toHaveBeenCalledWith(
      jasmine.objectContaining({
        invite_subject: "You are invited to Triton Control",
        reset_subject: "Reset your Triton Control password",
      }),
    );
    expect(component.emailSettingsHaveUnsavedChanges()).toBeFalse();
    expect(component.emailMessage()).toBe("Email settings saved.");
  });

  it("SaveEmailSettings_RequiredFieldsMissing_HighlightsFieldsAndDoesNotCallApi", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await openEmailTab(fixture);
    component.emailSettings.inviteSubject = "";
    component.emailSettings.resetExpiryMinutes = 1;

    // Act
    await component.saveEmailSettings();
    fixture.detectChanges();

    // Assert
    const publicUrlField = (fixture.nativeElement as HTMLElement)
      .querySelector("#email-public-url")
      ?.parentElement?.closest("mat-form-field");
    const publicUrlOutline = publicUrlField?.querySelector<HTMLElement>(
      ".mdc-notched-outline__leading",
    );
    const publicUrlLabel = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(
      'label[for="email-public-url"]',
    );
    expect(updateEmailSettingsSpy).not.toHaveBeenCalled();
    expect(publicUrlField?.classList.contains("email-field-invalid")).toBeTrue();
    expect(getComputedStyle(publicUrlOutline!).borderColor).toBe("rgb(185, 28, 28)");
    expect(getComputedStyle(publicUrlLabel!).color).toBe("rgb(185, 28, 28)");
    expect(component.emailAdvancedExpanded).toBeTrue();
    expect(component.emailTemplatesExpanded).toBeTrue();
    expect(component.emailMessageTone()).toBe("error");
    expect(component.emailMessage()).toBe("Complete the highlighted email settings before saving.");
  });

  it("SaveEmailSettings_ApiRejects_ShowsBackendValidationMessage", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      public_app_url: "https://control.example.test",
      smtp_host: "smtp.example.test",
      sender_email: "noreply@example.test",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    updateEmailSettingsSpy.and.returnValue(
      throwError(() => ({
        error: { detail: [{ msg: "SMTP configuration was rejected." }] },
      })),
    );
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await openEmailTab(fixture);
    component.emailSettings.senderName = "Updated sender";

    // Act
    await component.saveEmailSettings();
    fixture.detectChanges();

    // Assert
    const alert = (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]');
    expect(updateEmailSettingsSpy).toHaveBeenCalled();
    expect(component.emailMessageTone()).toBe("error");
    expect(component.emailMessage()).toBe("SMTP configuration was rejected.");
    expect(alert?.textContent).toContain("SMTP configuration was rejected.");
    expect(component.emailSaveAttempted()).toBeTrue();
  });

  it("SaveEmailSettings_DbManagedSmtp_RemainsEnabledForExplicitResave", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "disabled",
      read_only: false,
    };
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await fixture.whenStable();
    component.emailSettings.deliveryMode = "smtp";
    component.emailSettings.smtpHost = "smtp.example.com";
    component.emailSettings.senderEmail = "noreply@example.com";
    component.emailSettings.publicAppUrl = "https://control.example.com";
    component.smtpPassword = "secret";
    component.testRecipient = "admin@example.com";

    // Act
    await component.saveEmailSettings();
    await component.saveEmailSettings();

    // Assert
    expect(updateEmailSettingsSpy).toHaveBeenCalledTimes(2);
    expect(getEmailSettingsSpy).toHaveBeenCalledTimes(1);
    expect(component.emailSettingsHaveUnsavedChanges()).toBeFalse();
    expect(component.canSaveEmailSettings()).toBeTrue();
    expect(component.canSendTestEmail()).toBeTrue();
    expect(component.emailMessage()).toBe("Email settings saved.");
  });

  it("SaveEmailSettings_RequestCompletes_ImmediatelyRestoresReactiveState", async () => {
    // Arrange
    emailSettingsResponse = {
      config_source: "db",
      delivery_mode: "smtp",
      smtp_host: "smtp.example.com",
      sender_email: "noreply@example.com",
      public_app_url: "https://control.example.com",
      invite_subject: "You are invited to Triton Control",
      reset_subject: "Reset your Triton Control password",
      read_only: false,
    };
    const pendingSave = new Subject<any>();
    updateEmailSettingsSpy.and.returnValue(pendingSave);
    const fixture = TestBed.createComponent(SettingsPageComponent);
    const component = fixture.componentInstance;
    await openEmailTab(fixture);
    component.emailSettings.senderName = "Updated sender";

    // Act
    const request = component.saveEmailSettings();
    expect(component.emailSaving()).toBeTrue();
    pendingSave.next({ ...emailSettingsResponse, sender_name: "Updated sender" });
    pendingSave.complete();
    await request;

    // Assert
    expect(component.emailSaving()).toBeFalse();
    expect(component.canSaveEmailSettings()).toBeTrue();
    expect(component.emailMessage()).toBe("Email settings saved.");
  });
});
