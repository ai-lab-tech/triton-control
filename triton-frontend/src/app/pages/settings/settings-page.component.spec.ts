/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { provideStore, provideState } from "@ngrx/store";
import { provideEffects } from "@ngrx/effects";
import { SettingsPageComponent } from "./settings-page.component";
import { AuthService } from "../../shared/auth/auth.service";
import { SettingsEffects } from "../../state/settings/settings.effects";
import { SETTINGS_FEATURE_KEY, settingsReducer } from "../../state/settings/settings.reducer";
import { UsersService } from "../../api/generated";
import { of } from "rxjs";

describe("SettingsPageComponent", () => {
  let emailSettingsResponse: Record<string, unknown>;
  let testEmailSpy: jasmine.Spy;
  let authServiceMock: {
    getOidcSettings: jasmine.Spy;
    saveOidcSettings: jasmine.Spy;
  };

  beforeEach(() => {
    emailSettingsResponse = {
      config_source: "env",
      delivery_mode: "disabled",
      read_only: true,
    };
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
      imports: [SettingsPageComponent],
      providers: [
        provideStore(),
        provideState(SETTINGS_FEATURE_KEY, settingsReducer),
        provideEffects([SettingsEffects]),
        { provide: AuthService, useValue: authServiceMock },
        {
          provide: UsersService,
          useValue: {
            getEmailSettingsEndpointApiAuthEmailSettingsGet: () => of(emailSettingsResponse),
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

    // Act
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
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

    // Act
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
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
    fixture.detectChanges();
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
});
