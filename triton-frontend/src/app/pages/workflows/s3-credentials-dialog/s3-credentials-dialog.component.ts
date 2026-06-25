import { Component, inject, signal } from "@angular/core";
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
};

type CreateWorkflowS3CredentialRequest = {
  name: string;
  access_key_id: string;
  secret_access_key: string;
};

@Component({
  selector: "app-s3-credentials-dialog",
  standalone: true,
  imports: [
    FormsModule,
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
  credentialName = "";
  accessKeyId = "";
  secretAccessKey = "";

  constructor() {
    void this.loadCredentials();
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
      this.accessKeyId.trim().length > 0 &&
      this.secretAccessKey.trim().length > 0
    );
  }

  async createCredential(): Promise<void> {
    if (!this.canCreate()) {
      this.message.set("Name, Access Key ID and Secret Access Key are required.");
      return;
    }

    this.credentialsSaving.set(true);
    this.message.set("");
    try {
      const payload: CreateWorkflowS3CredentialRequest = {
        name: this.credentialName.trim(),
        access_key_id: this.accessKeyId.trim(),
        secret_access_key: this.secretAccessKey.trim(),
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
    this.credentialName = "";
    this.accessKeyId = "";
    this.secretAccessKey = "";
  }
}
