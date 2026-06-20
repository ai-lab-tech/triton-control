import { ErrorHandler, Injectable, inject } from "@angular/core";

import { ErrorLogReporterService } from "./error-log-reporter.service";

@Injectable()
export class FrontendErrorHandler implements ErrorHandler {
  private readonly reporter = inject(ErrorLogReporterService);

  handleError(error: unknown): void {
    this.reporter.reportError(error);
    console.error(error);
  }
}
