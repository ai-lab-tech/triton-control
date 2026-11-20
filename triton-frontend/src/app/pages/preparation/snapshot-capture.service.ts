import { inject, Injectable } from "@angular/core";
import { HttpClient, HttpErrorResponse } from "@angular/common/http";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";
import { Instance } from "../instances/instances.data";
import { InstancePods } from "../instances/detail/workload.types";
import { PreparationApiService, SnapshotRequest } from "./preparation-api.service";

interface SavedCapture {
  payload: string;
  key: string;
}

@Injectable({ providedIn: "root" })
export class SnapshotCaptureService {
  private readonly api = inject(PreparationApiService);
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  private readonly memory = new Map<string, SavedCapture | null>();

  request(scope: string): SnapshotRequest | null {
    const saved = this.read(scope);
    return saved ? (JSON.parse(saved.payload) as SnapshotRequest) : null;
  }

  private read(scope: string): SavedCapture | null {
    if (this.memory.has(scope)) return this.memory.get(scope) ?? null;
    try {
      const saved = JSON.parse(sessionStorage.getItem(scope) || "null") as SavedCapture | null;
      if (saved && typeof saved.payload === "string" && typeof saved.key === "string") {
        const request = JSON.parse(saved.payload) as SnapshotRequest | null;
        if (request && typeof request.title === "string" && Array.isArray(request.models)) {
          this.memory.set(scope, saved);
          return saved;
        }
      }
    } catch {
      // Invalid or unavailable storage must not discard a current in-memory request.
    }
    return null;
  }

  clear(scope: string) {
    // Retain the tombstone if storage removal fails, so stale data cannot be revived.
    this.memory.set(scope, null);
    try {
      sessionStorage.removeItem(scope);
    } catch {
      // New captures in this session use the cleared in-memory state.
    }
  }

  async submit(scope: string, instance: Instance, draft: SnapshotRequest) {
    let saved = this.read(scope);
    const replay = saved !== null;
    if (!saved) {
      const request = structuredClone(draft);
      if (instance.isSelfDeployed && instance.deploymentRuntime === "kubernetes") {
        const pods = await firstValueFrom(
          this.http.get<InstancePods>(
            `${this.base}/api/instances/${encodeURIComponent(instance.id)}/pods`,
          ),
        );
        if (!pods.managed || !pods.deployment || pods.deployment.stale)
          throw new Error(
            "Fresh deployment identity is required. Refresh the experiment and retry.",
          );
        request.deployment_uid = pods.deployment.uid;
        request.expected_generation = pods.deployment.generation;
      }
      saved = { payload: JSON.stringify(request), key: crypto.randomUUID() };
      this.memory.set(scope, saved);
      try {
        sessionStorage.setItem(scope, JSON.stringify(saved));
      } catch {
        // The memory copy remains authoritative if writes fail or storage is full.
      }
    }
    try {
      return await firstValueFrom(
        this.api.capture(instance.id, JSON.parse(saved.payload) as SnapshotRequest, saved.key),
      );
    } catch (error) {
      // A first-attempt admission rejection cannot have reserved this new key.
      // Keep uncertain requests even if a later replay is denied by current policy.
      if (
        !replay &&
        error instanceof HttpErrorResponse &&
        [400, 401, 403, 404, 422].includes(error.status)
      )
        this.clear(scope);
      throw error;
    }
  }
}
