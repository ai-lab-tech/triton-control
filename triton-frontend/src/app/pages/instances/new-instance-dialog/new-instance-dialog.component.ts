import { Component, inject } from "@angular/core";

import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { Store } from "@ngrx/store";

import { createInstanceRequested } from "../../../state/instances-list/instances-list.actions";

@Component({
  selector: "app-new-instance-dialog",
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  templateUrl: "./new-instance-dialog.component.html",
  styleUrl: "./new-instance-dialog.component.scss",
})
export class NewInstanceDialogComponent {
  private readonly store = inject(Store);
  private readonly dialogRef = inject(MatDialogRef<NewInstanceDialogComponent>);

  name = "";
  endpoint = "";
  metricsEndpoint = "";
  verifySsl = false;
  caCertificate = "";

  close() {
    this.dialogRef.close();
  }

  save() {
    const url = this.endpoint.trim();
    if (!url) {
      return;
    }

    const name = this.name.trim() || undefined;
    this.store.dispatch(
      createInstanceRequested({
        url,
        name,
        verifySsl: this.verifySsl,
        caCertificate: this.verifySsl ? this.caCertificate.trim() : "",
        metricsUrl: this.metricsEndpoint.trim() || undefined,
      }),
    );
    this.dialogRef.close();
  }

  onCertificateFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      this.caCertificate = `${reader.result ?? ""}`;
      input.value = "";
    };
    reader.readAsText(file);
  }
}
