import { Component, input } from "@angular/core";

import { MatCardModule } from "@angular/material/card";
import { MatIconModule } from "@angular/material/icon";

@Component({
  selector: "app-stat-card",
  standalone: true,
  imports: [MatCardModule, MatIconModule],
  styleUrl: "./stat-card.component.scss",
  templateUrl: "./stat-card.component.html",
})
export class StatCardComponent {
  readonly icon = input.required<string>();
  readonly label = input.required<string>();
  readonly value = input.required<string | number>();
  readonly trend = input<string>();
  readonly tone = input<"teal" | "amber" | "rose" | "sky">("teal");
}
