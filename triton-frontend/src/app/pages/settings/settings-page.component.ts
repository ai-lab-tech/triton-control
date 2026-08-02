import { Component, inject } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { MatTabsModule } from "@angular/material/tabs";
import { firstValueFrom } from "rxjs";
import { type EmailSettingsDTO, UsersService } from "../../api/generated";

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

const DEFAULT_INVITE_SUBJECT = "You are invited to Triton Control";
const DEFAULT_RESET_SUBJECT = "Reset your Triton Control password";
const EMAIL_VALIDATION_FIELDS = [
  "publicAppUrl",
  "smtpHost",
  "smtpPort",
  "senderEmail",
  "inviteExpiryMinutes",
  "resetExpiryMinutes",
  "inviteSubject",
  "resetSubject",
] as const;
type EmailValidationField = (typeof EMAIL_VALIDATION_FIELDS)[number];

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
    MatTabsModule,
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
  emailTesting = false;
  emailMessage = "";
  emailMessageTone: "success" | "error" | "info" = "info";
  emailSaveAttempted = false;
  emailTemplatesExpanded = false;
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
    inviteSubject: DEFAULT_INVITE_SUBJECT,
    inviteTextTemplate: "",
    inviteHtmlTemplate: "",
    resetSubject: DEFAULT_RESET_SUBJECT,
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

  canSaveEmailSettings(): boolean {
    return this.canEditEmailSettings() && !this.emailSaving;
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
      !this.emailTesting &&
      this.testRecipient.trim().length > 0 &&
      !this.emailSettingsHaveUnsavedChanges()
    );
  }

  emailFieldError(field: EmailValidationField): string {
    return this.emailSaveAttempted ? this.getEmailFieldError(field) : "";
  }

  smtpSecurityError(): string {
    if (
      this.emailSaveAttempted &&
      this.emailSettings.deliveryMode === "smtp" &&
      this.emailSettings.smtpTlsMode === "none" &&
      !this.emailSettings.smtpAllowInsecure
    ) {
      return "Confirm that you accept unencrypted SMTP transport.";
    }
    return "";
  }

  async saveEmailSettings(): Promise<void> {
    if (!this.canSaveEmailSettings()) return;
    this.emailSaveAttempted = true;
    const fieldErrors = new Map(
      EMAIL_VALIDATION_FIELDS.map((field) => [field, this.getEmailFieldError(field)]),
    );
    if (fieldErrors.get("inviteSubject") || fieldErrors.get("resetSubject")) {
      this.emailTemplatesExpanded = true;
    }
    const validationError = [...fieldErrors.values()].find(Boolean) || this.smtpSecurityError();
    if (validationError) {
      this.emailMessage = "Complete the highlighted email settings before saving.";
      this.emailMessageTone = "error";
      return;
    }
    this.emailSaving = true;
    this.emailMessage = "";
    this.emailMessageTone = "info";
    try {
      const savedSettings = await firstValueFrom(
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
      this.applyEmailSettings(savedSettings);
      this.emailMessage = "Email settings saved.";
      this.emailMessageTone = "success";
      this.emailSaveAttempted = false;
    } catch (error: unknown) {
      this.emailMessage = this.apiErrorMessage(error, "Failed to save email settings.");
      this.emailMessageTone = "error";
    } finally {
      this.emailSaving = false;
    }
  }

  async sendTestEmail(): Promise<void> {
    if (!this.canSendTestEmail()) {
      return;
    }
    this.emailTesting = true;
    this.emailMessageTone = "info";
    try {
      const response = await firstValueFrom(
        this.usersApi.testEmailEndpointApiAuthEmailSettingsTestPost({
          recipient: this.testRecipient,
        }),
      );
      this.emailMessage = response.message ?? "SMTP server accepted the test message.";
      this.emailMessageTone = "success";
      await this.loadEmailSettings();
    } catch (error: unknown) {
      this.emailMessage = this.apiErrorMessage(error, "Test email failed.");
      this.emailMessageTone = "error";
    } finally {
      this.emailTesting = false;
    }
  }

  private async loadEmailSettings(): Promise<void> {
    try {
      const value = await firstValueFrom(
        this.usersApi.getEmailSettingsEndpointApiAuthEmailSettingsGet(),
      );
      this.applyEmailSettings(value);
    } catch {
      this.emailMessage = "Failed to load email settings.";
      this.emailMessageTone = "error";
    }
  }

  private getEmailFieldError(field: EmailValidationField): string {
    const mode = this.emailSettings.deliveryMode;
    if (mode === "disabled") return "";

    switch (field) {
      case "publicAppUrl": {
        const value = this.emailSettings.publicAppUrl.trim();
        if (!value) return "Public application URL is required.";
        try {
          const url = new URL(value);
          return ["http:", "https:"].includes(url.protocol) && !!url.host
            ? ""
            : "Enter an absolute HTTP(S) URL.";
        } catch {
          return "Enter an absolute HTTP(S) URL.";
        }
      }
      case "smtpHost":
        return mode === "smtp" && !this.emailSettings.smtpHost.trim()
          ? "SMTP host is required."
          : "";
      case "smtpPort":
        return mode === "smtp" &&
          (!Number.isInteger(Number(this.emailSettings.smtpPort)) ||
            Number(this.emailSettings.smtpPort) < 1 ||
            Number(this.emailSettings.smtpPort) > 65535)
          ? "SMTP port must be between 1 and 65535."
          : "";
      case "senderEmail":
        if (mode !== "smtp") return "";
        if (!this.emailSettings.senderEmail.trim()) return "Sender email is required.";
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.emailSettings.senderEmail.trim())
          ? ""
          : "Enter a valid sender email address.";
      case "inviteExpiryMinutes":
        return this.isIntegerInRange(this.emailSettings.inviteExpiryMinutes, 5, 10080)
          ? ""
          : "Invite lifetime must be between 5 and 10080 minutes.";
      case "resetExpiryMinutes":
        return this.isIntegerInRange(this.emailSettings.resetExpiryMinutes, 5, 1440)
          ? ""
          : "Reset lifetime must be between 5 and 1440 minutes.";
      case "inviteSubject":
        return this.subjectError(this.emailSettings.inviteSubject, "Invitation");
      case "resetSubject":
        return this.subjectError(this.emailSettings.resetSubject, "Password reset");
    }
  }

  private isIntegerInRange(value: number, minimum: number, maximum: number): boolean {
    const numericValue = Number(value);
    return Number.isInteger(numericValue) && numericValue >= minimum && numericValue <= maximum;
  }

  private subjectError(value: string, label: string): string {
    if (!value.trim()) return `${label} subject is required.`;
    return value.length <= 255 ? "" : `${label} subject must not exceed 255 characters.`;
  }

  private apiErrorMessage(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } })?.error?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item: unknown) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) {
            const message = (item as { msg?: unknown }).msg;
            return typeof message === "string" ? message : "";
          }
          return "";
        })
        .filter(Boolean);
      if (messages.length) return messages.join(" ");
    }
    return fallback;
  }

  private applyEmailSettings(value: EmailSettingsDTO): void {
    const subjectsNeedDefaults = !value.invite_subject?.trim() || !value.reset_subject?.trim();
    const tlsModeNeedsCorrection =
      value.delivery_mode === "smtp" && value.smtp_port === 465 && value.smtp_tls_mode !== "tls";
    const smtpTlsMode = tlsModeNeedsCorrection ? "tls" : (value.smtp_tls_mode ?? "starttls");
    this.emailSettings = {
      configSource: value.config_source as "db" | "env",
      deliveryMode: (value.delivery_mode ?? "disabled") as "disabled" | "manual-link" | "smtp",
      smtpHost: value.smtp_host ?? "",
      smtpPort: value.smtp_port ?? 587,
      smtpTlsMode: smtpTlsMode as "starttls" | "tls" | "none",
      smtpAllowInsecure: !!value.smtp_allow_insecure,
      smtpUsername: value.smtp_username ?? "",
      smtpPasswordConfigured: !!value.smtp_password_configured,
      senderEmail: value.sender_email ?? "",
      senderName: value.sender_name ?? "Triton Control",
      publicAppUrl: value.public_app_url ?? "",
      caCertificate: value.ca_certificate ?? "",
      inviteExpiryMinutes: value.invite_expiry_minutes ?? 1440,
      resetExpiryMinutes: value.reset_expiry_minutes ?? 30,
      inviteSubject: value.invite_subject?.trim() || DEFAULT_INVITE_SUBJECT,
      inviteTextTemplate: value.invite_text_template ?? "",
      inviteHtmlTemplate: value.invite_html_template ?? "",
      resetSubject: value.reset_subject?.trim() || DEFAULT_RESET_SUBJECT,
      resetTextTemplate: value.reset_text_template ?? "",
      resetHtmlTemplate: value.reset_html_template ?? "",
      lastStatus: value.last_status ?? "not_tested",
      lastStatusMessage: value.last_status_message ?? "",
    };
    this.savedEmailSettingsSnapshot =
      subjectsNeedDefaults || tlsModeNeedsCorrection ? "" : JSON.stringify(this.emailSettings);
  }
}
