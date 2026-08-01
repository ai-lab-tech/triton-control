import { Component, OnInit, inject } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, RouterLink } from "@angular/router";
import { firstValueFrom } from "rxjs";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";

import { UsersService } from "../../api/generated";
import {
  EMAIL_POLICY_MESSAGE,
  isValidEmail,
  isValidPassword,
  PASSWORD_POLICY_MESSAGE,
} from "../../shared/password-policy";

type LifecycleMode = "activate" | "reset" | "forgot";

@Component({
  selector: "app-account-lifecycle-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  templateUrl: "./account-lifecycle-page.component.html",
  styleUrl: "./account-lifecycle-page.component.scss",
})
export class AccountLifecyclePageComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(UsersService);

  readonly mode = (this.route.snapshot.data["mode"] ?? "forgot") as LifecycleMode;
  token = this.route.snapshot.queryParamMap.get("token") ?? "";
  email = "";
  password = "";
  confirmPassword = "";
  targetEmail = "";
  loading = false;
  validLink = false;
  completed = false;
  message = "";
  error = "";

  async ngOnInit(): Promise<void> {
    if (this.mode === "forgot") return;
    if (!this.token) {
      this.error = "This link is invalid or expired.";
      return;
    }
    this.loading = true;
    try {
      const result =
        this.mode === "activate"
          ? await firstValueFrom(
              this.api.inspectInvitationEndpointApiAuthInvitationsInspectPost({
                token: this.token,
              }),
            )
          : await firstValueFrom(
              this.api.inspectResetEndpointApiAuthPasswordResetsInspectPost({
                token: this.token,
              }),
            );
      this.validLink = !!result.valid;
      this.targetEmail = result.email ?? "";
      if (!this.validLink) this.error = "This link is invalid or expired.";
    } catch {
      this.error = "This link is invalid or expired.";
    } finally {
      this.loading = false;
    }
  }

  async submitPassword(): Promise<void> {
    this.error = "";
    if (!isValidPassword(this.password)) {
      this.error = PASSWORD_POLICY_MESSAGE;
      return;
    }
    if (this.password !== this.confirmPassword) {
      this.error = "Passwords do not match.";
      return;
    }
    this.loading = true;
    try {
      const payload = { token: this.token, password: this.password };
      if (this.mode === "activate") {
        await firstValueFrom(
          this.api.activateInvitationEndpointApiAuthInvitationsActivatePost(payload),
        );
        this.message = "Your account is active. You can now sign in.";
      } else {
        await firstValueFrom(
          this.api.completeResetEndpointApiAuthPasswordResetsCompletePost(payload),
        );
        this.message = "Your password has been reset. You can now sign in.";
      }
      this.completed = true;
      this.validLink = false;
      this.token = "";
      this.password = "";
      this.confirmPassword = "";
    } catch {
      this.error = "This link is invalid or expired.";
    } finally {
      this.loading = false;
    }
  }

  async requestReset(): Promise<void> {
    this.error = "";
    if (!isValidEmail(this.email)) {
      this.error = EMAIL_POLICY_MESSAGE;
      return;
    }
    this.loading = true;
    try {
      await firstValueFrom(
        this.api.forgotPasswordEndpointApiAuthForgotPasswordPost({ email: this.email.trim() }),
      );
      this.completed = true;
      this.message = "If the account is eligible, password reset instructions will be sent.";
    } catch {
      this.error = "Self-service password recovery is unavailable.";
    } finally {
      this.loading = false;
    }
  }
}
