import { Component, DestroyRef, inject, input, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatDialog } from "@angular/material/dialog";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { UserRow } from "../../../state/users/users.reducer";
import { AccessGroup, AccessGroupsApiService } from "./access-groups-api.service";
import {
  AccessGroupsDialogComponent,
  AccessGroupsDialogData,
} from "./access-groups-dialog.component";

@Component({
  selector: "app-access-groups-panel",
  imports: [MatButtonModule, MatCardModule],
  templateUrl: "./access-groups-panel.component.html",
  styleUrl: "./access-groups-panel.component.scss",
})
export class AccessGroupsPanelComponent {
  private readonly api = inject(AccessGroupsApiService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);
  readonly users = input.required<UserRow[]>();
  readonly usersLoading = input(false);
  readonly groups = signal<AccessGroup[]>([]);
  readonly loading = signal(false);
  readonly error = signal("");

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set("");
    this.api
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (groups) => {
          this.groups.set(groups);
          this.loading.set(false);
        },
        error: (error) => {
          this.error.set(mapApiErrorMessage(error, "Could not load access groups."));
          this.loading.set(false);
        },
      });
  }

  open(group?: AccessGroup): void {
    this.dialog
      .open<AccessGroupsDialogComponent, AccessGroupsDialogData, boolean>(
        AccessGroupsDialogComponent,
        {
          data: { users: this.users(), groupId: group?.id },
          width: "36rem",
          maxWidth: "95vw",
        },
      )
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((changed) => {
        if (changed || group) this.load();
      });
  }
}
