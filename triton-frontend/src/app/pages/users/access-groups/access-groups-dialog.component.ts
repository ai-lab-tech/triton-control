import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MAT_DIALOG_DATA, MatDialogModule } from "@angular/material/dialog";
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
  readonly data: { users: UserRow[]; account?: UserRow } = inject(MAT_DIALOG_DATA);
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

  async reload(): Promise<void> {
    await this.run(async () => {
      const { groups, instances } = await firstValueFrom(
        forkJoin({ groups: this.api.list(), instances: this.api.instances() }),
      );
      this.groups.set(groups);
      this.instances.set(instances);
      this.choose(groups.find((group) => group.id === this.selected?.id));
      this.loaded.set(true);
    });
  }

  choose(group?: AccessGroup): void {
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
    if (!assignment.name || this.busy()) return;
    await this.run(async () => {
      const group = await firstValueFrom(this.api.save(assignment, this.selected));
      this.groups.update((groups) => [...groups.filter((item) => item.id !== group.id), group]);
      this.choose(group);
    });
  }

  async remove(group: AccessGroup): Promise<void> {
    if (!this.confirmingDelete || this.busy()) return;
    await this.run(async () => {
      await firstValueFrom(this.api.delete(group));
      this.groups.update((groups) => groups.filter((item) => item.id !== group.id));
      this.choose();
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
