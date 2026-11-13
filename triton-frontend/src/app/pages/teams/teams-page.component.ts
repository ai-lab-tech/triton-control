import { Component, computed, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatCheckboxModule } from "@angular/material/checkbox";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom, forkJoin } from "rxjs";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { Team, TeamEvent, TeamMember, TeamsApiService, TeamUser } from "./teams-api.service";

@Component({
  selector: "app-teams-page",
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: "./teams-page.component.html",
  styleUrl: "./teams-page.component.scss",
})
export class TeamsPageComponent {
  private readonly api = inject(TeamsApiService);
  readonly teams = signal<Team[]>([]);
  readonly users = signal<TeamUser[]>([]);
  readonly selected = signal<Team | null>(null);
  readonly members = signal<TeamMember[]>([]);
  readonly history = signal<TeamEvent[]>([]);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly availableUsers = computed(() =>
    this.users().filter(
      (user) =>
        user.is_active &&
        !this.members().some((member) => member.user_id === user.id && member.is_active),
    ),
  );
  newName = "";
  draftName = "";
  draftActive = true;
  selectedUserId: number | null = null;

  constructor() {
    void this.refresh();
  }

  async refresh(): Promise<void> {
    await this.run(async () => {
      const result = await firstValueFrom(
        forkJoin({ teams: this.api.list(), users: this.api.users() }),
      );
      this.teams.set(result.teams);
      this.users.set(result.users);
      const team = result.teams.find((item) => item.id === this.selected()?.id) ?? result.teams[0];
      if (team) await this.loadDetails(team);
      else this.selected.set(null);
    });
  }

  async selectTeam(team: Team): Promise<void> {
    await this.run(() => this.loadDetails(team));
  }

  async createTeam(): Promise<void> {
    if (!this.newName.trim()) return;
    await this.run(async () => {
      const team = await firstValueFrom(this.api.create({ name: this.newName, is_active: true }));
      this.newName = "";
      this.teams.set(await firstValueFrom(this.api.list()));
      await this.loadDetails(team);
    });
  }

  async saveTeam(): Promise<void> {
    const selected = this.selected();
    if (!selected || !this.draftName.trim()) return;
    await this.run(async () => {
      const team = await firstValueFrom(
        this.api.update(selected.id, { name: this.draftName, is_active: this.draftActive }),
      );
      this.teams.set(await firstValueFrom(this.api.list()));
      await this.loadDetails(team);
    });
  }

  async setMember(userId: number | null, active: boolean): Promise<void> {
    const team = this.selected();
    if (!team || userId === null || (active && !team.is_active)) return;
    await this.run(async () => {
      await firstValueFrom(this.api.setMember(team.id, userId, active));
      await this.loadDetails(team);
    });
  }

  userLabel(id: number): string {
    const user = this.users().find((item) => item.id === id);
    return user ? `${user.name} (${user.email})` : `Deleted user #${id}`;
  }

  eventLabel(event: TeamEvent): string {
    const subject = event.subject_user_id === null ? "" : this.userLabel(event.subject_user_id);
    switch (event.action) {
      case "team_created":
        return "Team created";
      case "member_added":
        return `Membership added: ${subject}`;
      case "member_removed":
        return `Membership revoked: ${subject}`;
      default: {
        const before = event.changes.before;
        const after = event.changes.after;
        return before && after
          ? `${before.name} (${before.is_active ? "active" : "inactive"}) → ${after.name} (${after.is_active ? "active" : "inactive"})`
          : "Team updated";
      }
    }
  }

  private async loadDetails(team: Team): Promise<void> {
    this.selected.set(team);
    this.members.set([]);
    this.history.set([]);
    this.selectedUserId = null;
    this.draftName = team.name;
    this.draftActive = team.is_active;
    const result = await firstValueFrom(
      forkJoin({ members: this.api.members(team.id), history: this.api.history(team.id) }),
    );
    this.members.set(result.members);
    this.history.set(result.history);
  }

  private async run(action: () => Promise<void>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      await action();
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Team operation failed. Refresh and try again."));
    } finally {
      this.busy.set(false);
    }
  }
}
