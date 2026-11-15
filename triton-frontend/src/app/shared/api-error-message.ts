import { HttpErrorResponse } from "@angular/common/http";

export function isBackendUnavailable(error: unknown): boolean {
  return error instanceof HttpErrorResponse && [0, 502, 503, 504].includes(error.status);
}

export function mapApiErrorMessage(error: unknown, fallback: string): string {
  if (isBackendUnavailable(error)) {
    return "Connection to the backend is unavailable. Please try again.";
  }
  if (error instanceof HttpErrorResponse) {
    const body = error.error as Record<string, unknown> | null;
    const detail = body?.["detail"];
    if (typeof detail === "string" && detail.trim()) {
      return detail.trim();
    }

    if (Array.isArray(detail)) {
      const text = detail
        .map((item) =>
          typeof (item as Record<string, unknown>)?.["msg"] === "string"
            ? ((item as Record<string, unknown>)["msg"] as string)
            : "Validation error",
        )
        .join(" | ")
        .trim();
      if (text) {
        return text;
      }
    }

    const apiError = body?.["error"];
    if (typeof apiError === "string" && apiError.trim()) {
      return apiError.trim();
    }

    if (typeof error.error === "string" && error.error.trim()) {
      return error.error.trim();
    }

    if (typeof error.message === "string" && error.message.trim()) {
      return error.message.trim();
    }
  }

  return fallback;
}
