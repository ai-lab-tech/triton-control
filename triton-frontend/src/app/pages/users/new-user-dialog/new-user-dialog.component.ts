import { Component, inject } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom } from "rxjs";
import { UsersService } from "../../../api/generated";

import { Store } from "@ngrx/store";
import { type UserRow } from "../../../state/users/users.reducer";
import { createUserRequested, usersPageOpened } from "../../../state/users/users.actions";
import {
  selectUsers,
  selectUsersInstances,
  selectUsersOidcEnabled,
} from "../../../state/users/users.selectors";
import { EMAIL_POLICY_MESSAGE, isValidEmail } from "../../../shared/password-policy";

@Component({
  selector: "app-new-user-dialog",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: "./new-user-dialog.component.html",
  styleUrl: "./new-user-dialog.component.scss",
})
export class NewUserDialogComponent {
  private readonly store = inject(Store);
  private readonly dialogRef = inject(MatDialogRef<NewUserDialogComponent>);
  private readonly usersApi = inject(UsersService);
  private readonly dialogData = inject<{ emailLifecycleAvailable?: boolean }>(MAT_DIALOG_DATA, {
    optional: true,
  });

  readonly instances = toSignal(this.store.select(selectUsersInstances), {
    initialValue: [] as string[],
  });
  readonly users = toSignal(this.store.select(selectUsers), {
    initialValue: [] as UserRow[],
  });
  readonly oidcEnabled = toSignal(this.store.select(selectUsersOidcEnabled), {
    initialValue: false,
  });

  newUser = {
    name: "",
    email: "",
    role: "viewer",
    auth: "local" as "local" | "oidc",
    instances: [] as string[],
    creationMode: "invite" as "invite" | "inactive",
  };
  error = "";
  manualLink = "";
  notice = "";
  saving = false;
  invitationAvailable = false;

  constructor() {
    // Set auth method once oidcEnabled is known (may already be in store)
    const oidc = this.oidcEnabled();
    this.newUser.auth = oidc ? "oidc" : "local";
    this.invitationAvailable = !oidc && !!this.dialogData?.emailLifecycleAvailable;
    this.newUser.creationMode = this.invitationAvailable ? "invite" : "inactive";
  }

  get canSave(): boolean {
    if (this.newUser.name.trim().length === 0 || this.newUser.email.trim().length === 0) {
      return false;
    }
    if (!isValidEmail(this.newUser.email)) {
      return false;
    }
    if (this.emailExists(this.newUser.email)) {
      return false;
    }
    return this.newUser.role.trim().length > 0;
  }

  close(): void {
    this.dialogRef.close();
  }

  save(): void {
    this.error = "";
    if (!this.canSave) {
      if (this.newUser.email.trim().length > 0 && !isValidEmail(this.newUser.email)) {
        this.error = EMAIL_POLICY_MESSAGE;
        return;
      }
      if (this.emailExists(this.newUser.email)) {
        this.error = "Email already exists.";
        return;
      }
      return;
    }
    const oidc = this.oidcEnabled();
    if (!oidc && this.newUser.creationMode === "invite") {
      if (this.invitationAvailable) {
        void this.invite();
        return;
      }
      this.newUser.creationMode = "inactive";
    }
    this.store.dispatch(
      createUserRequested({
        name: this.newUser.name.trim(),
        email: this.newUser.email.trim(),
        role: this.newUser.role,
        auth: oidc ? "oidc" : "local",
        creationMode: !oidc ? "inactive" : undefined,
        instances: [...this.newUser.instances],
      }),
    );
    this.dialogRef.close();
  }

  async copyManualLink(): Promise<void> {
    if (!this.manualLink) return;
    await navigator.clipboard.writeText(this.manualLink);
    this.notice = "Activation link copied. It will not be shown after this dialog closes.";
  }

  private async invite(): Promise<void> {
    this.saving = true;
    this.error = "";
    try {
      const response = await firstValueFrom(
        this.usersApi.inviteUserEndpointApiAuthInvitationsPost({
          name: this.newUser.name.trim(),
          email: this.newUser.email.trim(),
          role: this.newUser.role,
          assigned_instances: [...this.newUser.instances],
        }),
      );
      this.manualLink = response.manual_link ?? "";
      this.notice = response.delivered
        ? "Invitation email accepted by the SMTP server."
        : "Invitation created. Transfer the activation link through a trusted channel.";
      this.store.dispatch(usersPageOpened());
      if (response.delivered) {
        this.dialogRef.close();
      }
    } catch (error: unknown) {
      const detail = (error as { error?: { detail?: string } })?.error?.detail;
      this.error = detail || "Failed to invite user.";
    } finally {
      this.saving = false;
    }
  }

  private emailExists(email: string): boolean {
    const normalized = email.trim().toLowerCase();
    return (this.users() ?? []).some((user) => user.email.trim().toLowerCase() === normalized);
  }
}
