import { Component, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { MatSelectModule } from "@angular/material/select";
import { HttpClient } from "@angular/common/http";
import { FormsModule } from "@angular/forms";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";

import { BASE_PATH } from "../../../api/generated/index";
import { mapApiErrorMessage } from "../../../shared/api-error-message";

type WorkflowS3CredentialDTO = {
  id: number;
  name: string;
  namespace: string;
  secret_name: string;
  access_key_id: string;
  created_at: string;
  updated_at: string;
  s3_profile_id?: number | null;
  s3_profile_name?: string;
  sync_error?: string;
  last_synced_at?: string | null;
};

type CreateWorkflowS3CredentialRequest = {
  name: string;
  access_key_id?: string;
  secret_access_key?: string;
  s3_profile_id?: number;
  ca_certificate?: string;
};

type ProfileChoice = { id: number; name: string; endpoint: string; bucket: string; region: string };

@Component({
  selector: "app-s3-credentials-dialog",
  standalone: true,
  imports: [
    FormsModule,
    MatSelectModule,
    DatePipe,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
  ],
  templateUrl: "./s3-credentials-dialog.component.html",
  styleUrl: "./s3-credentials-dialog.component.scss",
})
export class S3CredentialsDialogComponent {
  private readonly dialogRef = inject(MatDialogRef<S3CredentialsDialogComponent>);
  private readonly http = inject(HttpClient);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  readonly credentials = signal<WorkflowS3CredentialDTO[]>([]);
  readonly credentialsLoading = signal(false);
  readonly credentialsSaving = signal(false);
  readonly deletingCredentialId = signal<number | null>(null);
  readonly message = signal("");
  readonly showForm = signal(false);
  readonly profiles = signal<ProfileChoice[]>([]);
  readonly syncingCredentialId = signal<number | null>(null);
  selectedProfileId: number | null = null;
  credentialName = "";
  accessKeyId = "";
  secretAccessKey = "";
  caCertificate = "";

  async loadCaCertificate(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    try {
      if (file.size > 262144) {
        this.message.set("CA certificate bundle must be at most 256 KiB.");
        return;
      }
      this.caCertificate = await file.text();
      this.message.set("");
    } catch {
      this.message.set("Could not read the certificate file.");
    } finally {
      input.value = "";
    }
  }

  constructor() {
    void this.loadCredentials();
    void this.loadProfiles();
  }

  async refresh(): Promise<void> {
    await Promise.all([this.loadCredentials(), this.loadProfiles()]);
  }

  close(): void {
    this.dialogRef.close();
  }

  toggleForm(): void {
    this.showForm.update((open) => !open);
    this.message.set("");
    if (!this.showForm()) {
      this.resetForm();
    }
  }

  canCreate(): boolean {
    return (
      !this.credentialsSaving() &&
      this.credentialName.trim().length > 0 &&
      (this.selectedProfileId !== null ||
        (this.accessKeyId.trim().length > 0 && this.secretAccessKey.trim().length > 0))
    );
  }

  async createCredential(): Promise<void> {
    if (!this.canCreate()) {
      this.message.set("Enter a name and select an S3 profile or provide access keys.");
      return;
    }

    this.credentialsSaving.set(true);
    this.message.set("");
    try {
      const payload: CreateWorkflowS3CredentialRequest = {
        name: this.credentialName.trim(),
        ...(this.selectedProfileId !== null
          ? { s3_profile_id: this.selectedProfileId }
          : {
              access_key_id: this.accessKeyId.trim(),
              secret_access_key: this.secretAccessKey.trim(),
              ...(this.caCertificate.trim() ? { ca_certificate: this.caCertificate.trim() } : {}),
            }),
      };
      await firstValueFrom(
        this.http.post<WorkflowS3CredentialDTO>(
          `${this.basePath}/api/workflows/s3-credentials`,
          payload,
        ),
      );
      this.message.set("Workflow S3 credential created.");
      this.resetForm();
      this.showForm.set(false);
      await this.loadCredentials();
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Failed to create workflow S3 credential."));
    } finally {
      this.credentialsSaving.set(false);
    }
  }

  async deleteCredential(credential: WorkflowS3CredentialDTO): Promise<void> {
    this.deletingCredentialId.set(Number(credential.id));
    this.message.set("");
    try {
      await firstValueFrom(
        this.http.delete(`${this.basePath}/api/workflows/s3-credentials/${credential.id}`),
      );
      this.message.set("Workflow S3 credential deleted.");
      await this.loadCredentials();
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Failed to delete workflow S3 credential."));
    } finally {
      this.deletingCredentialId.set(null);
    }
  }

  canSync(credential: WorkflowS3CredentialDTO): boolean {
    return this.profiles().some((p) => p.id === credential.s3_profile_id);
  }

  selectProfile(): void {
    const profile = this.profiles().find((p) => p.id === this.selectedProfileId);
    if (profile && !this.credentialName.trim()) this.credentialName = profile.name;
  }

  async syncCredential(credential: WorkflowS3CredentialDTO): Promise<void> {
    this.syncingCredentialId.set(credential.id);
    try {
      const result = await firstValueFrom(
        this.http.post<WorkflowS3CredentialDTO>(
          `${this.basePath}/api/workflows/s3-credentials/${credential.id}/sync`,
          {},
        ),
      );
      this.message.set(result.sync_error || "Workflow Secret synchronized.");
      await this.loadCredentials();
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Failed to synchronize workflow Secret."));
    } finally {
      this.syncingCredentialId.set(null);
    }
  }

  private async loadProfiles(): Promise<void> {
    try {
      this.profiles.set(
        await firstValueFrom(
          this.http.get<ProfileChoice[]>(`${this.basePath}/api/workflows/s3-profile-choices`),
        ),
      );
    } catch {
      this.message.set("Could not load S3 profiles. Manual entry is still available.");
    }
  }

  private async loadCredentials(): Promise<void> {
    this.credentialsLoading.set(true);
    try {
      const rows = await firstValueFrom(
        this.http.get<WorkflowS3CredentialDTO[]>(`${this.basePath}/api/workflows/s3-credentials`),
      );
      this.credentials.set(rows || []);
    } catch (error) {
      this.credentials.set([]);
      this.message.set(mapApiErrorMessage(error, "Failed to load workflow S3 credentials."));
    } finally {
      this.credentialsLoading.set(false);
    }
  }

  private resetForm(): void {
    this.selectedProfileId = null;
    this.credentialName = "";
    this.accessKeyId = "";
    this.secretAccessKey = "";
    this.caCertificate = "";
  }
}
