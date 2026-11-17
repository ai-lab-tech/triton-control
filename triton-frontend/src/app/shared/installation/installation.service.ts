import { Injectable, Injector, computed, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";

export interface InstallationCapabilities {
  mode: "development" | "production";
  capabilities: Record<string, boolean>;
  explanation: string;
}

@Injectable({ providedIn: "root" })
export class InstallationService {
  private readonly injector = inject(Injector);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");
  readonly snapshot = signal<InstallationCapabilities | null>(null);
  readonly label = computed(
    () => this.snapshot()?.explanation ?? "Installation policy unavailable",
  );

  allows(capability: string): boolean {
    return this.snapshot()?.capabilities[capability] === true;
  }

  async load(): Promise<boolean> {
    try {
      const result = await firstValueFrom(
        this.injector
          .get(HttpClient)
          .get<InstallationCapabilities>(`${this.basePath}/api/capabilities`, {
            withCredentials: true,
          }),
      );
      if (!["development", "production"].includes(result.mode) || !result.capabilities) {
        throw new Error("Invalid installation capabilities");
      }
      this.snapshot.set(result);
      return true;
    } catch {
      this.snapshot.set(null);
      return false;
    }
  }
}
