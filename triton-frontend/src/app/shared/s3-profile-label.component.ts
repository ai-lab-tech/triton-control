import { Component, input } from "@angular/core";

@Component({
  selector: "app-s3-profile-label",
  standalone: true,
  template: `
    <span class="profile-name">{{ profile().name }}</span>
    @if (profile().used_by_mlflow) {
      <span class="usage-badge" aria-label="Usage: MLflow artifact store"
        >MLflow artifact store</span
      >
    }
  `,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      max-width: 100%;
    }
    .profile-name {
      overflow-wrap: anywhere;
    }
    .usage-badge {
      padding: 0.15rem 0.5rem;
      border: 1px solid #99f6e4;
      border-radius: 999px;
      background: #f0fdfa;
      color: #115e59;
      font-size: 0.75rem;
      font-weight: 500;
      line-height: 1.4;
      white-space: nowrap;
    }
  `,
})
export class S3ProfileLabelComponent {
  readonly profile = input.required<{ name: string; used_by_mlflow?: boolean }>();
}
