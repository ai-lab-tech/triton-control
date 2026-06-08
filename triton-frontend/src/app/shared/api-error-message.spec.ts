import { HttpErrorResponse } from "@angular/common/http";
import { mapApiErrorMessage } from "./api-error-message";

describe("mapApiErrorMessage", () => {
  it("NonHttpError_ReturnsGivenFallback", () => {
    const result = mapApiErrorMessage(new Error("plain error"), "fallback message");
    expect(result).toBe("fallback message");
  });

  it("HttpError_DetailIsString_ReturnsDetail", () => {
    const error = new HttpErrorResponse({ status: 400, error: { detail: "  bad request  " } });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("bad request");
  });

  it("HttpError_DetailIsEmptyString_SkipsDetailFallsToNext", () => {
    const error = new HttpErrorResponse({
      status: 400,
      error: { detail: "   ", error: "fallback-error" },
    });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("fallback-error");
  });

  it("HttpError_DetailIsArray_JoinsMessages", () => {
    const detail = [{ msg: "field required" }, { msg: "invalid value" }];
    const error = new HttpErrorResponse({ status: 422, error: { detail } });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toContain("field required");
    expect(result).toContain("invalid value");
  });

  it("HttpError_DetailIsArrayWithNonStringMsg_UsesValidationError", () => {
    const detail = [{ msg: 42 }];
    const error = new HttpErrorResponse({ status: 422, error: { detail } });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("Validation error");
  });

  it("HttpError_DetailIsArrayEmpty_FallsThrough", () => {
    const error = new HttpErrorResponse({ status: 422, error: { detail: [], error: "api-error" } });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("api-error");
  });

  it("HttpError_ErrorFieldIsString_ReturnsErrorField", () => {
    const error = new HttpErrorResponse({ status: 500, error: { error: "internal server error" } });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("internal server error");
  });

  it("HttpError_ErrorIsStringBody_ReturnsBodyString", () => {
    const error = new HttpErrorResponse({ status: 503, error: "Service Unavailable" });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result).toBe("Service Unavailable");
  });

  it("HttpError_NoBodyButHasMessage_ReturnsMessage", () => {
    const error = new HttpErrorResponse({ status: 0, statusText: "Unknown Error" });
    const result = mapApiErrorMessage(error, "fallback");
    expect(result.length).toBeGreaterThan(0);
  });

  it("NullError_ReturnsFallback", () => {
    const result = mapApiErrorMessage(null, "default");
    expect(result).toBe("default");
  });

  it("StringError_ReturnsFallback", () => {
    const result = mapApiErrorMessage("some string error", "default");
    expect(result).toBe("default");
  });
});
