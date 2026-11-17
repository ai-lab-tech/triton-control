import { Component, inject } from "@angular/core";
import { RouterLink } from "@angular/router";
import { InstallationService } from "./installation.service";

@Component({
  standalone: true,
  imports: [RouterLink],
  template: `<section style="padding: 2rem" aria-labelledby="policy-title">
    <h1 id="policy-title">This action is unavailable</h1>
    <p>{{ installation.label() }}</p>
    <p>
      Production keeps serving inventory, health, logs and authorized artifact reads available.
      Interactive tools run in development. Production changes require independent approval; direct
      changes remain disabled until that workflow is available.
    </p>
    <p>If installation policy could not be loaded, retry after restoring the connection.</p>
    <a routerLink="/instances">Return to serving instances</a>
  </section>`,
})
export class InstallationRestrictedComponent {
  readonly installation = inject(InstallationService);
}
