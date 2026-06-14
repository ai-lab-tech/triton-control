import { Injectable, signal } from "@angular/core";

@Injectable({ providedIn: "root" })
export class ChromeService {
  readonly topbarHidden = signal(false);

  hideTopbar(): void {
    this.topbarHidden.set(true);
  }

  showTopbar(): void {
    this.topbarHidden.set(false);
  }
}
