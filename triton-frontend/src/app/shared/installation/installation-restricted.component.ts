import { Component, inject } from "@angular/core";
import { RouterLink } from "@angular/router";
import { InstallationService } from "./installation.service";

@Component({
  standalone: true,
  imports: [RouterLink],
  template: `<section class="card" aria-labelledby="policy-title">
    <h1 class="page-title" id="policy-title">This action is unavailable</h1>
    <p>{{ installation.label() }}</p>
    <p>
      Production keeps serving inventory, health, logs and authorized artifact reads available.
      Interactive tools run in development. Production changes require independent approval; direct
      changes remain disabled until that workflow is available.
    </p>
    <p>If installation policy could not be loaded, retry after restoring the connection.</p>
    <a routerLink="/instances">Return to serving instances</a>
  </section>`,
  styles: [
    `
      .card {
        max-width: 48rem;
        margin: auto;
        padding: var(--card-padding);
      }
      a {
        color: #0f766e;
        text-decoration: underline;
      }
    `,
  ],
})
export class InstallationRestrictedComponent {
  readonly installation = inject(InstallationService);
}
