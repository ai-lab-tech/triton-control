import { HttpBackend, HttpClient, HttpErrorResponse } from "@angular/common/http";
import { Injectable, inject } from "@angular/core";
import { BASE_PATH } from "../api/generated/index";
import { AuthStore } from "./auth/auth.store";
import { AuthService } from "./auth/auth.service";

export type ErrorLogEvent = {
  id: number;
  source: "frontend" | "backend";
  level: string;
  message: string;
  detail?: string | null;
  path?: string | null;
  method?: string | null;
  status_code?: number | null;
  user_email?: string | null;
  user_id?: number | null;
  user_agent?: string | null;
  created_at: string;
};

type FrontendErrorPayload = {
  level?: string;
  message: string;
  detail?: string;
  path?: string;
  method?: string;
  status_code?: number;
  user_agent?: string;
};

@Injectable({ providedIn: "root" })
export class ErrorLogReporterService {
  private readonly auth = inject(AuthService);
  private readonly authState = inject(AuthStore);
  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  private readonly endpoint = `${this.basePath}/api/admin/error-logs`;

  list(source = "", limit = 100) {
    const params: Record<string, string> = { limit: `${limit}` };
    if (source) {
      params["source"] = source;
    }
    return this.http.get<ErrorLogEvent[]>(this.endpoint, {
      params,
      headers: this.authHeaders(),
      withCredentials: true,
    });
  }

  reportError(error: unknown): void {
    const err = this.unwrap(error);
    this.report({
      level: "ERROR",
      message: err.message || "Frontend error",
      detail: err.stack || `${error}`,
      path: window.location.pathname,
      user_agent: navigator.userAgent,
    });
  }

  reportHttpError(error: HttpErrorResponse, method: string, path: string): void {
    this.report({
      level: "ERROR",
      message: `HTTP ${error.status} ${error.statusText || "Error"}`,
      detail: this.safeStringify(error.error) || error.message,
      path,
      method,
      status_code: error.status,
      user_agent: navigator.userAgent,
    });
  }

  private report(payload: FrontendErrorPayload): void {
    if (!this.authState.isLoggedIn()) {
      return;
    }
    this.http
      .post<ErrorLogEvent>(`${this.endpoint}/frontend`, this.sanitize(payload), {
        headers: this.authHeaders(),
        withCredentials: true,
      })
      .subscribe({ error: () => undefined });
  }

  private authHeaders(): Record<string, string> {
    const token = this.auth.getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  private unwrap(error: unknown): Error {
    if (error instanceof Error) {
      return error;
    }
    if (typeof error === "object" && error && "rejection" in error) {
      const rejection = (error as { rejection?: unknown }).rejection;
      if (rejection instanceof Error) {
        return rejection;
      }
    }
    return new Error(`${error}`);
  }

  private sanitize(payload: FrontendErrorPayload): FrontendErrorPayload {
    return {
      ...payload,
      message: this.redact(payload.message).slice(0, 1000),
      detail: this.redact(payload.detail ?? "").slice(0, 6000),
      path: payload.path?.slice(0, 500),
      method: payload.method?.slice(0, 20),
      user_agent: payload.user_agent?.slice(0, 500),
    };
  }

  private safeStringify(value: unknown): string {
    if (value == null) {
      return "";
    }
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value);
    } catch {
      return `${value}`;
    }
  }

  private redact(value: string): string {
    return value
      .split(/\r?\n/)
      .map((line) =>
        /authorization|token|secret|password|cookie/i.test(line) ? "[redacted]" : line,
      )
      .join("\n");
  }
}
