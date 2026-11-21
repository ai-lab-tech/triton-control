import { Component, input, linkedSignal, output } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { TaskWorkspaceOptions, WorkspaceSizing } from "./handoffs-api.service";

@Component({
  selector: "app-task-workspace-options",
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  styles: `
    :host,
    .workspace-fields {
      display: grid;
      gap: 8px;
    }
    mat-form-field {
      width: 100%;
    }
    p {
      margin: 0;
      overflow-wrap: anywhere;
    }
    button {
      justify-self: start;
    }
  `,
  template: `
    @if (sizing().existing_storage_size; as storage) {
      <p>Workspace image: {{ sizing().existing_image }}</p>
      <p>GPUs: {{ sizing().existing_gpu_count ?? 0 }}</p>
      <p>CPU: {{ sizing().existing_cpu || "Cluster default" }}</p>
      <p>RAM: {{ sizing().existing_memory || "Cluster default" }}</p>
      <p>Writable storage: {{ storage }}</p>
    } @else {
      <p>
        Recommended storage: {{ sizing().recommended_gib }} GiB. Maximum:
        {{ sizing().maximum_gib }} GiB.
      </p>
      <div class="workspace-fields">
        <mat-form-field appearance="outline">
          <mat-label>Workspace image</mat-label>
          <input matInput [(ngModel)]="draft().image" [disabled]="busy()" required />
        </mat-form-field>
        <mat-form-field appearance="outline">
          <mat-label>GPU count</mat-label>
          <input
            matInput
            type="number"
            min="0"
            step="1"
            [(ngModel)]="draft().gpu"
            [disabled]="busy()"
            required
          />
          <mat-hint>Use 0 for CPU only.</mat-hint>
        </mat-form-field>
        <mat-form-field appearance="outline">
          <mat-label>CPU cores (optional)</mat-label>
          <input
            matInput
            type="number"
            min="0.001"
            step="0.001"
            [(ngModel)]="draft().cpu"
            [disabled]="busy()"
            placeholder="2"
          />
        </mat-form-field>
        <mat-form-field appearance="outline">
          <mat-label>RAM (MiB, optional)</mat-label>
          <input
            matInput
            type="number"
            min="1"
            step="1"
            [(ngModel)]="draft().memory"
            [disabled]="busy()"
            placeholder="4096"
          />
        </mat-form-field>
        <mat-form-field appearance="outline">
          <mat-label>Writable workspace storage (GiB)</mat-label>
          <input
            matInput
            type="number"
            step="1"
            [min]="sizing().recommended_gib"
            [max]="sizing().maximum_gib"
            [(ngModel)]="draft().storage"
            [disabled]="busy()"
            required
          />
        </mat-form-field>
      </div>
      <p>Leave CPU and RAM blank to use cluster defaults.</p>
      @if (sizing().recommended_gib > sizing().maximum_gib) {
        <p role="alert">
          This task exceeds the workspace storage maximum. Contact an administrator.
        </p>
      }
    }
    <button
      mat-flat-button
      type="button"
      color="primary"
      (click)="confirm()"
      [disabled]="busy() || !valid()"
    >
      {{ sizing().existing_storage_size ? "Reopen workspace" : "Create workspace" }}
    </button>
  `,
})
export class TaskWorkspaceOptionsComponent {
  readonly sizing = input.required<WorkspaceSizing>();
  readonly busy = input(false);
  readonly confirmOptions = output<TaskWorkspaceOptions | undefined>();
  readonly draft = linkedSignal(() => ({
    image: this.sizing().default_image,
    gpu: 0 as number | null,
    cpu: null as number | null,
    memory: null as number | null,
    storage: this.sizing().recommended_gib as number | null,
  }));

  valid(): boolean {
    const size = this.sizing();
    if (size.existing_storage_size) return true;
    const { image, gpu, cpu, memory, storage } = this.draft();
    return (
      !!image.trim() &&
      !/\s/.test(image.trim()) &&
      gpu !== null &&
      Number.isSafeInteger(gpu) &&
      gpu >= 0 &&
      (cpu === null ||
        (cpu > 0 &&
          Number.isSafeInteger(Math.round(cpu * 1000)) &&
          Math.abs(cpu * 1000 - Math.round(cpu * 1000)) < 0.000001)) &&
      (memory === null || (Number.isSafeInteger(memory) && memory >= 1)) &&
      storage !== null &&
      Number.isInteger(storage) &&
      storage >= size.recommended_gib &&
      storage <= size.maximum_gib
    );
  }

  confirm(): void {
    if (this.busy() || !this.valid()) return;
    if (this.sizing().existing_storage_size) {
      this.confirmOptions.emit(undefined);
      return;
    }
    const { image, gpu, cpu, memory, storage } = this.draft();
    this.confirmOptions.emit({
      image: image.trim(),
      gpu_count: gpu!,
      cpu: cpu === null ? undefined : `${Math.round(cpu * 1000)}m`,
      memory: memory === null ? undefined : `${memory}Mi`,
      storage_size: `${storage}Gi`,
    });
  }
}
