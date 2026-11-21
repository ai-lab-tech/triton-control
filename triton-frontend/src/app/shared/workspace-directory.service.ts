import { Injectable, inject, signal } from "@angular/core";
import { firstValueFrom } from "rxjs";
import { CodeServerDTO, DevelopmentService } from "../api/generated";

@Injectable({ providedIn: "root" })
export class WorkspaceDirectory {
  private readonly api = inject(DevelopmentService);
  private readonly entries = signal<CodeServerDTO[]>([]);
  readonly workspaces = this.entries.asReadonly();
  private pending: Promise<CodeServerDTO[]> | undefined;

  refresh(): Promise<CodeServerDTO[]> {
    if (this.pending) return this.pending;
    const pending = firstValueFrom(this.api.listCodeServersApiDevelopmentGet())
      .then((workspaces) => {
        if (this.pending === pending) this.entries.set(workspaces);
        return workspaces;
      })
      .finally(() => {
        if (this.pending === pending) this.pending = undefined;
      });
    this.pending = pending;
    return pending;
  }

  clear(): void {
    this.pending = undefined;
    this.entries.set([]);
  }

  label(workspace: CodeServerDTO): string {
    const name = workspace.task_title || workspace.name;
    const duplicate = this.workspaces().some(
      (item) => item.id !== workspace.id && (item.task_title || item.name) === name,
    );
    return duplicate ? `${name} (#${workspace.id})` : name;
  }

  remove(id: number): void {
    this.entries.update((items) => items.filter((workspace) => workspace.id !== id));
  }

  upsert(workspace: CodeServerDTO): void {
    this.entries.update((items) =>
      items.some((item) => item.id === workspace.id)
        ? items.map((item) => (item.id === workspace.id ? workspace : item))
        : [workspace, ...items],
    );
  }
}
