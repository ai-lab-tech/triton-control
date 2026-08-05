import { Component, OnInit, inject, signal } from "@angular/core";
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
  readonly targetEmail = signal("");
  readonly loading = signal(false);
  readonly validLink = signal(false);
  readonly completed = signal(false);
  readonly message = signal("");
  readonly error = signal("");

  async ngOnInit(): Promise<void> {
    if (this.mode === "forgot") return;
    if (!this.token) {
      this.error.set("This link is invalid or expired.");
      return;
    }
    this.loading.set(true);
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
      this.validLink.set(!!result.valid);
      this.targetEmail.set(result.email ?? "");
      if (!this.validLink()) this.error.set("This link is invalid or expired.");
    } catch {
      this.error.set("This link is invalid or expired.");
    } finally {
      this.loading.set(false);
    }
  }

  async submitPassword(): Promise<void> {
    this.error.set("");
    if (!isValidPassword(this.password)) {
      this.error.set(PASSWORD_POLICY_MESSAGE);
      return;
    }
    if (this.password !== this.confirmPassword) {
      this.error.set("Passwords do not match.");
      return;
    }
    this.loading.set(true);
    try {
      const payload = { token: this.token, password: this.password };
      if (this.mode === "activate") {
        await firstValueFrom(
          this.api.activateInvitationEndpointApiAuthInvitationsActivatePost(payload),
        );
        this.message.set("Your account is active. You can now sign in.");
      } else {
        await firstValueFrom(
          this.api.completeResetEndpointApiAuthPasswordResetsCompletePost(payload),
        );
        this.message.set("Your password has been reset. You can now sign in.");
      }
      this.completed.set(true);
      this.validLink.set(false);
      this.token = "";
      this.password = "";
      this.confirmPassword = "";
    } catch {
      this.error.set("This link is invalid or expired.");
    } finally {
      this.loading.set(false);
    }
  }

  async requestReset(): Promise<void> {
    this.error.set("");
    if (!isValidEmail(this.email)) {
      this.error.set(EMAIL_POLICY_MESSAGE);
      return;
    }
    this.loading.set(true);
    try {
      await firstValueFrom(
        this.api.forgotPasswordEndpointApiAuthForgotPasswordPost({ email: this.email.trim() }),
      );
      this.completed.set(true);
      this.message.set("If the account is eligible, password reset instructions will be sent.");
    } catch {
      this.error.set("Self-service password recovery is unavailable.");
    } finally {
      this.loading.set(false);
    }
  }
}
