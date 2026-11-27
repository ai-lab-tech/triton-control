import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom, forkJoin } from "rxjs";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { UserRow } from "../../../state/users/users.reducer";
import {
  AccessGroup,
  AccessGroupsApiService,
  GroupAssignment,
  InstanceOption,
} from "./access-groups-api.service";

export interface AccessGroupsDialogData {
  users: UserRow[];
  account?: UserRow;
  groupId?: string;
}

function sameSelection(current: number[], saved: number[]): boolean {
  return current.length === saved.length && current.every((id) => saved.includes(id));
}

@Component({
  selector: "app-access-groups-dialog",
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: "./access-groups-dialog.component.html",
  styles: ["mat-form-field { display: block; margin-top: 0.75rem; }"],
})
export class AccessGroupsDialogComponent {
  private readonly api = inject(AccessGroupsApiService);
  private readonly dialog =
    inject<MatDialogRef<AccessGroupsDialogComponent, boolean>>(MatDialogRef);
  readonly data: AccessGroupsDialogData = inject(MAT_DIALOG_DATA);
  readonly hasViewers = this.data.users.some((user) => user.role.trim().toLowerCase() === "viewer");
  readonly groups = signal<AccessGroup[]>([]);
  readonly instances = signal<InstanceOption[]>([]);
  readonly busy = signal(false);
  readonly loaded = signal(false);
  readonly error = signal("");
  selected?: AccessGroup;
  name = "";
  userIds: number[] = [];
  instanceIds: number[] = [];
  confirmingDelete = false;

  constructor() {
    void this.reload();
  }

  get hasEdits(): boolean {
    return (
      this.name.trim() !== (this.selected?.name ?? "") ||
      !sameSelection(this.userIds, this.selected?.user_ids ?? []) ||
      !sameSelection(this.instanceIds, this.selected?.instance_ids ?? [])
    );
  }

  canClose(result: unknown): boolean {
    return result === true || (!this.busy() && (result === false || !this.hasEdits));
  }

  async reload(): Promise<void> {
    await this.run(async () => {
      this.loaded.set(false);
      const { groups, instances } = await firstValueFrom(
        forkJoin({ groups: this.api.list(), instances: this.api.instances() }),
      );
      const group = groups.find((group) => group.id === this.data.groupId);
      if (this.data.groupId && !group) {
        this.error.set("This group was deleted. Close the dialog to refresh the list.");
        return;
      }
      this.groups.set(groups);
      this.instances.set(instances);
      this.choose(group);
      this.loaded.set(true);
    });
  }

  private choose(group?: AccessGroup): void {
    this.selected = group;
    this.name = group?.name ?? "";
    this.userIds = [...(group?.user_ids ?? [])];
    this.instanceIds = [...(group?.instance_ids ?? [])];
    this.confirmingDelete = false;
  }

  async save(): Promise<void> {
    const assignment: GroupAssignment = {
      name: this.name.trim(),
      user_ids: this.userIds,
      instance_ids: this.instanceIds,
    };
    if (!this.loaded() || !assignment.name || !this.hasEdits || this.busy()) return;
    await this.run(async () => {
      await firstValueFrom(this.api.save(assignment, this.selected));
      this.dialog.close(true);
    });
  }

  async remove(group: AccessGroup): Promise<void> {
    if (!this.confirmingDelete || this.busy()) return;
    await this.run(async () => {
      await firstValueFrom(this.api.delete(group));
      this.dialog.close(true);
    });
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set("");
    try {
      await action();
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not manage access groups."));
    } finally {
      this.busy.set(false);
    }
  }
}
