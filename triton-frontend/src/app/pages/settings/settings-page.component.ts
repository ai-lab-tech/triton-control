import { Component, inject } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom } from "rxjs";
import { UsersService } from "../../api/generated";

import { Store } from "@ngrx/store";
import { Actions, ofType } from "@ngrx/effects";
import { toSignal } from "@angular/core/rxjs-interop";

import { environment } from "../../../environments/environment";
import { type OidcSettings } from "../../shared/auth/auth.service";
import {
  oidcSettingsLoaded,
  oidcSettingsSaveRequested,
  oidcSettingsSaveSucceeded,
  settingsPageOpened,
} from "../../state/settings/settings.actions";
import {
  selectSettingsLoading,
  selectSettingsMessage,
  selectSettingsMessageTone,
  selectSettingsSaving,
} from "../../state/settings/settings.selectors";

@Component({
  selector: "app-settings-page",
  standalone: true,
  imports: [
    FormsModule,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  styleUrl: "./settings-page.component.scss",
  templateUrl: "./settings-page.component.html",
})
export class SettingsPageComponent {
  private readonly store = inject(Store);
  private readonly usersApi = inject(UsersService);
  private savedEmailSettingsSnapshot = "";

  readonly loading = toSignal(this.store.select(selectSettingsLoading), { initialValue: false });
  readonly saving = toSignal(this.store.select(selectSettingsSaving), { initialValue: false });
  readonly message = toSignal(this.store.select(selectSettingsMessage), { initialValue: "" });
  readonly messageTone = toSignal(this.store.select(selectSettingsMessageTone), {
    initialValue: "info" as const,
  });

  // Form draft — mutated directly by [(ngModel)]
  oidcEnabled = true;
  emailSaving = false;
  emailMessage = "";
  testRecipient = "";
  smtpPassword = "";
  emailSettings = {
    configSource: "env" as "db" | "env",
    deliveryMode: "disabled" as "disabled" | "manual-link" | "smtp",
    smtpHost: "",
    smtpPort: 587,
    smtpTlsMode: "starttls" as "starttls" | "tls" | "none",
    smtpAllowInsecure: false,
    smtpUsername: "",
    smtpPasswordConfigured: false,
    senderEmail: "",
    senderName: "Triton Control",
    publicAppUrl: "",
    caCertificate: "",
    inviteExpiryMinutes: 1440,
    resetExpiryMinutes: 30,
    inviteSubject: "You are invited to Triton Control",
    inviteTextTemplate: "",
    inviteHtmlTemplate: "",
    resetSubject: "Reset your Triton Control password",
    resetTextTemplate: "",
    resetHtmlTemplate: "",
    lastStatus: "not_tested",
    lastStatusMessage: "",
  };
  settings: OidcSettings = {
    oidcEnabled: true,
    issuer: "",
    clientId: "",
    clientSecret: "",
    clientSecretConfigured: false,
    redirectUri: "",
    scopes: "",
    strictDiscoveryDocumentValidation: true,
    caCertificate: "",
    apiBaseUrl: environment.apiBaseUrl,
    configSource: "db",
    kubernetesEnabled: false,
  };

  constructor() {
    const actions$ = inject(Actions);

    // Populate form when settings are loaded or saved
    actions$
      .pipe(ofType(oidcSettingsLoaded, oidcSettingsSaveSucceeded), takeUntilDestroyed())
      .subscribe(({ settings }) => {
        this.oidcEnabled = settings.oidcEnabled;
        this.settings = { ...settings };
      });

    this.store.dispatch(settingsPageOpened());
    void this.loadEmailSettings();
  }

  saveSettings(): void {
    if (!this.canSaveSettings()) return;

    this.store.dispatch(
      oidcSettingsSaveRequested({
        settings: { ...this.settings, oidcEnabled: this.oidcEnabled },
      }),
    );
  }

  isDbConfigSource(): boolean {
    return this.settings.configSource === "db";
  }

  canEditOidcSettings(): boolean {
    return this.isDbConfigSource();
  }

  canSaveSettings(): boolean {
    return this.canEditOidcSettings() && !this.loading() && !this.saving();
  }

  getClientSecretPlaceholder(): string {
    return this.settings.clientSecretConfigured ? "Stored secret configured" : "Client Secret";
  }

  onCertificateFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      this.settings.caCertificate = `${reader.result ?? ""}`;
      input.value = "";
    };
    reader.readAsText(file);
  }

  canEditEmailSettings(): boolean {
    return this.emailSettings.configSource === "db";
  }

  canEditEmailLifecycleConfiguration(): boolean {
    return this.canEditEmailSettings() && this.emailSettings.deliveryMode !== "disabled";
  }

  emailSettingsHaveUnsavedChanges(): boolean {
    return (
      !this.savedEmailSettingsSnapshot ||
      this.savedEmailSettingsSnapshot !== JSON.stringify(this.emailSettings) ||
      this.smtpPassword.length > 0
    );
  }

  canSendTestEmail(): boolean {
    return (
      this.emailSettings.deliveryMode === "smtp" &&
      !this.emailSaving &&
      this.testRecipient.trim().length > 0 &&
      !this.emailSettingsHaveUnsavedChanges()
    );
  }

  async saveEmailSettings(): Promise<void> {
    if (!this.canEditEmailSettings()) return;
    this.emailSaving = true;
    this.emailMessage = "";
    try {
      await firstValueFrom(
        this.usersApi.updateEmailSettingsEndpointApiAuthEmailSettingsPut({
          delivery_mode: this.emailSettings.deliveryMode,
          smtp_host: this.emailSettings.smtpHost,
          smtp_port: this.emailSettings.smtpPort,
          smtp_tls_mode: this.emailSettings.smtpTlsMode,
          smtp_allow_insecure: this.emailSettings.smtpAllowInsecure,
          smtp_username: this.emailSettings.smtpUsername,
          smtp_password: this.smtpPassword || undefined,
          sender_email: this.emailSettings.senderEmail,
          sender_name: this.emailSettings.senderName,
          public_app_url: this.emailSettings.publicAppUrl,
          ca_certificate: this.emailSettings.caCertificate,
          invite_expiry_minutes: this.emailSettings.inviteExpiryMinutes,
          reset_expiry_minutes: this.emailSettings.resetExpiryMinutes,
          invite_subject: this.emailSettings.inviteSubject,
          invite_text_template: this.emailSettings.inviteTextTemplate,
          invite_html_template: this.emailSettings.inviteHtmlTemplate,
          reset_subject: this.emailSettings.resetSubject,
          reset_text_template: this.emailSettings.resetTextTemplate,
          reset_html_template: this.emailSettings.resetHtmlTemplate,
        }),
      );
      this.smtpPassword = "";
      this.emailMessage = "Email settings saved.";
      await this.loadEmailSettings();
    } catch (error: unknown) {
      this.emailMessage =
        (error as { error?: { detail?: string } })?.error?.detail ??
        "Failed to save email settings.";
    } finally {
      this.emailSaving = false;
    }
  }

  async sendTestEmail(): Promise<void> {
    if (!this.canSendTestEmail()) {
      return;
    }
    this.emailSaving = true;
    try {
      const response = await firstValueFrom(
        this.usersApi.testEmailEndpointApiAuthEmailSettingsTestPost({
          recipient: this.testRecipient,
        }),
      );
      this.emailMessage = response.message ?? "SMTP server accepted the test message.";
      await this.loadEmailSettings();
    } catch (error: unknown) {
      this.emailMessage =
        (error as { error?: { detail?: string } })?.error?.detail ?? "Test email failed.";
    } finally {
      this.emailSaving = false;
    }
  }

  private async loadEmailSettings(): Promise<void> {
    try {
      const value = await firstValueFrom(
        this.usersApi.getEmailSettingsEndpointApiAuthEmailSettingsGet(),
      );
      this.emailSettings = {
        configSource: value.config_source as "db" | "env",
        deliveryMode: (value.delivery_mode ?? "disabled") as "disabled" | "manual-link" | "smtp",
        smtpHost: value.smtp_host ?? "",
        smtpPort: value.smtp_port ?? 587,
        smtpTlsMode: (value.smtp_tls_mode ?? "starttls") as "starttls" | "tls" | "none",
        smtpAllowInsecure: !!value.smtp_allow_insecure,
        smtpUsername: value.smtp_username ?? "",
        smtpPasswordConfigured: !!value.smtp_password_configured,
        senderEmail: value.sender_email ?? "",
        senderName: value.sender_name ?? "Triton Control",
        publicAppUrl: value.public_app_url ?? "",
        caCertificate: value.ca_certificate ?? "",
        inviteExpiryMinutes: value.invite_expiry_minutes ?? 1440,
        resetExpiryMinutes: value.reset_expiry_minutes ?? 30,
        inviteSubject: value.invite_subject ?? "",
        inviteTextTemplate: value.invite_text_template ?? "",
        inviteHtmlTemplate: value.invite_html_template ?? "",
        resetSubject: value.reset_subject ?? "",
        resetTextTemplate: value.reset_text_template ?? "",
        resetHtmlTemplate: value.reset_html_template ?? "",
        lastStatus: value.last_status ?? "not_tested",
        lastStatusMessage: value.last_status_message ?? "",
      };
      this.savedEmailSettingsSnapshot = JSON.stringify(this.emailSettings);
    } catch {
      this.emailMessage = "Failed to load email settings.";
    }
  }
}
