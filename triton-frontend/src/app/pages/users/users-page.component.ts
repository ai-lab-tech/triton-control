import { Component, inject } from "@angular/core";
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

import { Store } from "@ngrx/store";
import { Actions, ofType } from "@ngrx/effects";
import { toSignal } from "@angular/core/rxjs-interop";

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
  ],
  styleUrl: "./users-page.component.scss",
  templateUrl: "./users-page.component.html",
})
export class UsersPageComponent {
  private readonly store = inject(Store);
  private readonly dialog = inject(MatDialog);

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
  }

  openNewUserDialog(): void {
    this.dialog.open(NewUserDialogComponent, {
      width: "520px",
      panelClass: "custom-dialog",
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
}
