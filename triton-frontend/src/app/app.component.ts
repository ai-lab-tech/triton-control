import { Component, OnInit, inject } from "@angular/core";
import { RouterOutlet } from "@angular/router";
import { AuthService } from "./shared/auth/auth.service";

@Component({
  selector: "app-root",
  standalone: true,
  imports: [RouterOutlet],
  template: `<router-outlet></router-outlet>`,
})
export class AppComponent implements OnInit {
  private readonly auth = inject(AuthService);

  ngOnInit(): void {
    void this.auth.init().catch((err) => console.error("Auth init failed", err));
  }
}
