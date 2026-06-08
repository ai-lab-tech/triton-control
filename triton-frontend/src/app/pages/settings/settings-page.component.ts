import { Component, inject } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";

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
  ],
  styleUrl: "./settings-page.component.scss",
  templateUrl: "./settings-page.component.html",
})
export class SettingsPageComponent {
  private readonly store = inject(Store);

  readonly loading = toSignal(this.store.select(selectSettingsLoading), { initialValue: false });
  readonly saving = toSignal(this.store.select(selectSettingsSaving), { initialValue: false });
  readonly message = toSignal(this.store.select(selectSettingsMessage), { initialValue: "" });
  readonly messageTone = toSignal(this.store.select(selectSettingsMessageTone), {
    initialValue: "info" as const,
  });

  // Form draft — mutated directly by [(ngModel)]
  oidcEnabled = true;
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
}
