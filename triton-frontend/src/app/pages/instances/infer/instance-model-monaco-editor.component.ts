import { Component, computed, input, model } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MonacoEditorModule, NGX_MONACO_EDITOR_CONFIG } from "ngx-monaco-editor-v2";

@Component({
  selector: "app-instance-model-monaco-editor",
  standalone: true,
  imports: [FormsModule, MonacoEditorModule],
  providers: [
    {
      provide: NGX_MONACO_EDITOR_CONFIG,
      useValue: {
        baseUrl: "assets",
      },
    },
  ],
  template: `
    <ngx-monaco-editor
      class="editor"
      [options]="editorOptions()"
      [(ngModel)]="value"
    ></ngx-monaco-editor>
  `,
  styles: [
    `
      .editor {
        display: block;
        height: 340px;
        border-radius: 14px;
        overflow: hidden;
        box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.08);
      }

      @media (max-width: 768px) {
        .editor {
          height: 280px;
        }
      }
    `,
  ],
})
export class InstanceModelMonacoEditorComponent {
  readonly value = model("");
  readonly language = input("json");
  readonly readOnly = input(false);

  readonly editorOptions = computed(() => ({
    theme: "vs-dark",
    language: this.language(),
    readOnly: this.readOnly(),
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  }));
}
