import { Component, inject, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";

import { FormsModule } from "@angular/forms";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatTableModule } from "@angular/material/table";
import { MatChipsModule } from "@angular/material/chips";
import { MatDialog, MatDialogModule } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatSelectModule } from "@angular/material/select";
import { MatInputModule } from "@angular/material/input";
import { MatProgressSpinnerModule } from "@angular/material/progress-spinner";

import { Store } from "@ngrx/store";
import { Actions, ofType } from "@ngrx/effects";
import { toSignal } from "@angular/core/rxjs-interop";
import { firstValueFrom } from "rxjs";
import { UsersService } from "../../api/generated";

import { NewUserDialogComponent } from "./new-user-dialog/new-user-dialog.component";
import { type UserRow } from "../../state/users/users.reducer";
import {
  addInstanceToUserRequested,
  createUserSucceeded,
  deleteUserRequested,
  removeInstanceFromUserRequested,
  updateUserRoleFailed,
  updateUserRoleRequested,
  updateUserRoleSucceeded,
  usersDataLoaded,
  usersPageOpened,
} from "../../state/users/users.actions";
import {
  selectUsers,
  selectUsersError,
  selectUsersInstances,
  selectUsersLoading,
} from "../../state/users/users.selectors";

@Component({
  selector: "app-users-page",
  standalone: true,
  imports: [
    FormsModule,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatTableModule,
    MatChipsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatSelectModule,
    MatInputModule,
    MatProgressSpinnerModule,
  ],
  styleUrl: "./users-page.component.scss",
  templateUrl: "./users-page.component.html",
})
export class UsersPageComponent {
  private readonly store = inject(Store);
  private readonly dialog = inject(MatDialog);
  private readonly usersApi = inject(UsersService);

  readonly users = toSignal(this.store.select(selectUsers), { initialValue: [] as UserRow[] });
  readonly instances = toSignal(this.store.select(selectUsersInstances), {
    initialValue: [] as string[],
  });
  readonly loading = toSignal(this.store.select(selectUsersLoading), { initialValue: false });
  readonly error = toSignal(this.store.select(selectUsersError), { initialValue: "" });

  readonly displayedColumns = [
    "name",
    "email",
    "role",
    "status",
    "auth",
    "instances",
    "addInstance",
    "actions",
  ];
  readonly roles = ["admin", "member", "viewer"];

  // Local per-row form state ([(ngModel)] per row)
  pendingInstanceByEmail: Record<string, string> = {};
  pendingRoleByUserId: Record<number, string> = {};
  readonly lifecycleMessage = signal("");
  readonly manualLink = signal("");
  readonly lifecycleActionRunning = signal(false);
  readonly passwordResetUserId = signal<number | null>(null);
  readonly emailLifecycleAvailable = signal(false);
  readonly emailLifecycleStatus = signal<"loading" | "disabled" | "enabled" | "unavailable">(
    "loading",
  );

  constructor() {
    const actions$ = inject(Actions);

    // Sync pendingRoleByUserId when data loads or a role update settles
    actions$.pipe(ofType(usersDataLoaded), takeUntilDestroyed()).subscribe(({ users }) => {
      this.pendingRoleByUserId = Object.fromEntries(users.map((u) => [u.id, u.role]));
    });

    actions$.pipe(ofType(createUserSucceeded), takeUntilDestroyed()).subscribe(({ user }) => {
      this.pendingRoleByUserId = { ...this.pendingRoleByUserId, [user.id]: user.role };
    });

    actions$
      .pipe(ofType(updateUserRoleSucceeded), takeUntilDestroyed())
      .subscribe(({ userId, role }) => {
        this.pendingRoleByUserId = { ...this.pendingRoleByUserId, [userId]: role };
      });

    actions$
      .pipe(ofType(updateUserRoleFailed), takeUntilDestroyed())
      .subscribe(({ userId, prevRole }) => {
        this.pendingRoleByUserId = { ...this.pendingRoleByUserId, [userId]: prevRole };
      });

    this.store.dispatch(usersPageOpened());
    void this.loadEmailLifecycleAvailability();
  }

  get lifecycleActionDisabledReason(): string {
    switch (this.emailLifecycleStatus()) {
      case "loading":
        return "Checking account email lifecycle availability.";
      case "disabled":
        return "Enable manual-link or SMTP email lifecycle in Settings.";
      case "unavailable":
        return "Account email lifecycle availability could not be loaded.";
      default:
        return "";
    }
  }

  openNewUserDialog(): void {
    this.dialog.open(NewUserDialogComponent, {
      width: "520px",
      panelClass: "custom-dialog",
      data: {
        emailLifecycleAvailable: this.emailLifecycleAvailable(),
      },
    });
  }

  availableInstances(user: UserRow): string[] {
    return this.instances().filter((i) => !user.instances.includes(i));
  }

  addInstanceToUser(user: UserRow): void {
    const selected = this.pendingInstanceByEmail[user.email];
    if (!selected || user.instances.includes(selected)) {
      return;
    }
    const nextInstances = [...user.instances, selected];
    this.pendingInstanceByEmail[user.email] = "";
    this.store.dispatch(addInstanceToUserRequested({ userId: user.id, instances: nextInstances }));
  }

  removeInstanceFromUser(user: UserRow, instance: string): void {
    const nextInstances = user.instances.filter((i) => i !== instance);
    this.store.dispatch(
      removeInstanceFromUserRequested({ userId: user.id, instances: nextInstances }),
    );
  }

  deleteUser(user: UserRow): void {
    this.store.dispatch(deleteUserRequested({ userId: user.id, email: user.email }));
  }

  updateRole(user: UserRow, role: string): void {
    if (!role) {
      return;
    }
    if (role === user.role && user.isActive) {
      return;
    }
    this.store.dispatch(updateUserRoleRequested({ userId: user.id, role, prevRole: user.role }));
  }

  approveUser(user: UserRow): void {
    const role = this.pendingRoleByUserId[user.id] || user.role || "viewer";
    this.updateRole(user, role);
  }

  async reissueInvitation(user: UserRow): Promise<void> {
    if (!this.emailLifecycleAvailable() || this.lifecycleActionRunning()) {
      return;
    }
    await this.runLifecycleAction(async () => {
      const response = await firstValueFrom(
        this.usersApi.reissueInvitationEndpointApiAuthInvitationsUserIdReissuePost(user.id),
      );
      this.showLifecycleResponse(response, "Invitation reissued.");
    });
  }

  async revokeInvitation(user: UserRow): Promise<void> {
    if (!this.emailLifecycleAvailable() || this.lifecycleActionRunning()) {
      return;
    }
    await this.runLifecycleAction(async () => {
      await firstValueFrom(
        this.usersApi.revokeInvitationEndpointApiAuthInvitationsUserIdDelete(user.id),
      );
      this.lifecycleMessage.set("Invitation canceled.");
      this.manualLink.set("");
      this.store.dispatch(usersPageOpened());
    });
  }

  async issuePasswordReset(user: UserRow): Promise<void> {
    if (!this.emailLifecycleAvailable() || this.lifecycleActionRunning()) {
      return;
    }
    this.passwordResetUserId.set(user.id);
    try {
      await this.runLifecycleAction(async () => {
        this.lifecycleMessage.set(
          "Issuing password reset… Delivery may take a few seconds to complete.",
        );
        const response = await firstValueFrom(
          this.usersApi.adminResetEndpointApiAuthPasswordResetsPost({ user_id: user.id }),
        );
        this.showLifecycleResponse(response, "Password reset issued.");
      });
    } finally {
      this.passwordResetUserId.set(null);
    }
  }

  async copyManualLink(): Promise<void> {
    const link = this.manualLink();
    if (!link) return;
    await navigator.clipboard.writeText(link);
    this.lifecycleMessage.set("One-time link copied.");
  }

  private showLifecycleResponse(
    response: { manual_link?: string | null; delivered?: boolean },
    fallback: string,
  ): void {
    const link = response.manual_link ?? "";
    this.manualLink.set(link);
    this.lifecycleMessage.set(
      response.delivered
        ? "Email accepted by the SMTP server."
        : link
          ? "Copy this link now and transfer it through a trusted channel."
          : fallback,
    );
  }

  private async runLifecycleAction(action: () => Promise<void>): Promise<void> {
    if (this.lifecycleActionRunning()) {
      return;
    }
    this.lifecycleActionRunning.set(true);
    this.lifecycleMessage.set("");
    this.manualLink.set("");
    try {
      await action();
    } catch (error: unknown) {
      this.lifecycleMessage.set(
        (error as { error?: { detail?: string } })?.error?.detail ?? "Lifecycle action failed.",
      );
    } finally {
      this.lifecycleActionRunning.set(false);
    }
  }

  private async loadEmailLifecycleAvailability(): Promise<void> {
    try {
      const settings = await firstValueFrom(
        this.usersApi.getEmailSettingsEndpointApiAuthEmailSettingsGet(),
      );
      const available =
        settings.delivery_mode === "manual-link" || settings.delivery_mode === "smtp";
      this.emailLifecycleAvailable.set(available);
      this.emailLifecycleStatus.set(available ? "enabled" : "disabled");
    } catch {
      this.emailLifecycleAvailable.set(false);
      this.emailLifecycleStatus.set("unavailable");
    }
  }
}
