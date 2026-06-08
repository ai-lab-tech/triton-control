import { Component, inject } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";

import { Store } from "@ngrx/store";
import { type UserRow } from "../../../state/users/users.reducer";
import { createUserRequested } from "../../../state/users/users.actions";
import {
  selectUsers,
  selectUsersInstances,
  selectUsersOidcEnabled,
} from "../../../state/users/users.selectors";
import {
  EMAIL_POLICY_MESSAGE,
  isValidEmail,
  isValidPassword,
  PASSWORD_POLICY_MESSAGE,
} from "../../../shared/password-policy";

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
    password: "",
    instances: [] as string[],
  };
  error = "";

  constructor() {
    // Set auth method once oidcEnabled is known (may already be in store)
    const oidc = this.oidcEnabled();
    this.newUser.auth = oidc ? "oidc" : "local";
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
    if (
      !this.oidcEnabled() &&
      this.newUser.password.length > 0 &&
      !isValidPassword(this.newUser.password)
    ) {
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
      if (
        !this.oidcEnabled() &&
        this.newUser.password.length > 0 &&
        !isValidPassword(this.newUser.password)
      ) {
        this.error = PASSWORD_POLICY_MESSAGE;
      }
      return;
    }
    const oidc = this.oidcEnabled();
    this.store.dispatch(
      createUserRequested({
        name: this.newUser.name.trim(),
        email: this.newUser.email.trim(),
        role: this.newUser.role,
        auth: oidc ? "oidc" : "local",
        password: !oidc && this.newUser.password.length > 0 ? this.newUser.password : undefined,
        instances: [...this.newUser.instances],
      }),
    );
    this.dialogRef.close();
  }

  private emailExists(email: string): boolean {
    const normalized = email.trim().toLowerCase();
    return (this.users() ?? []).some((user) => user.email.trim().toLowerCase() === normalized);
  }
}
