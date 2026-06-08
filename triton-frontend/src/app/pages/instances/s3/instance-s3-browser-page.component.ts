import { Component, computed, inject, OnInit } from "@angular/core";
import { takeUntilDestroyed, toSignal } from "@angular/core/rxjs-interop";
import { Actions, ofType } from "@ngrx/effects";
import { Store } from "@ngrx/store";
import { firstValueFrom } from "rxjs";

import { ActivatedRoute, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { FormsModule } from "@angular/forms";
import { A11yModule } from "@angular/cdk/a11y";
import { MonacoEditorModule, NGX_MONACO_EDITOR_CONFIG } from "ngx-monaco-editor-v2";
import type * as Monaco from "monaco-editor";

import { InstancesService } from "../../../api/generated/index";
import { type BrowserEntry } from "../../../state/instances-s3/instances-s3.reducer";
import {
  s3EditorClosed,
  s3EditorContentLoadFailed,
  s3EditorContentLoaded,
  s3EditorOpenRequested,
  s3EditorSaveRequested,
  s3EditorSaveSucceeded,
  s3FileUploadRequested,
  s3NavigateTo,
  s3PageOpened,
} from "../../../state/instances-s3/instances-s3.actions";
import {
  selectS3BucketName,
  selectS3CurrentPath,
  selectS3EditorFileName,
  selectS3EditorFilePath,
  selectS3EditorLoading,
  selectS3EditorOpen,
  selectS3Entries,
  selectS3UploadFileName,
  selectS3UploadLoading,
  selectS3InstanceName,
  selectS3KnownFolderPaths,
} from "../../../state/instances-s3/instances-s3.selectors";
import { displayFailure } from "../../../state/shared/shared.actions";
import { AuthStore } from "../../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../../shared/api-error-message";

@Component({
  selector: "app-instance-s3-browser-page",
  standalone: true,
  imports: [
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    FormsModule,
    A11yModule,
    MonacoEditorModule,
  ],
  providers: [
    {
      provide: NGX_MONACO_EDITOR_CONFIG,
      useValue: {
        baseUrl: "assets",
      },
    },
  ],
  styleUrl: "./instance-s3-browser-page.component.scss",
  templateUrl: "./instance-s3-browser-page.component.html",
})
export class InstanceS3BrowserPageComponent implements OnInit {
  private readonly store = inject(Store);
  private readonly instancesApi = inject(InstancesService);
  private readonly route = inject(ActivatedRoute);
  private readonly actions$ = inject(Actions);
  private readonly auth = inject(AuthStore);

  readonly instanceId = computed(() => this.route.snapshot.paramMap.get("id"));
  readonly hasValidId = computed(() => {
    const id = (this.instanceId() ?? "").trim();
    return !!id && /^[0-9]+$/.test(id);
  });

  // Store state
  readonly instanceName = toSignal(this.store.select(selectS3InstanceName), { initialValue: "" });
  readonly bucketName = toSignal(this.store.select(selectS3BucketName), { initialValue: "" });
  readonly currentPath = toSignal(this.store.select(selectS3CurrentPath), { initialValue: "/" });
  readonly entries = toSignal(this.store.select(selectS3Entries), { initialValue: [] });
  readonly knownFolderPaths = toSignal(this.store.select(selectS3KnownFolderPaths), {
    initialValue: ["/"],
  });
  readonly editorOpen = toSignal(this.store.select(selectS3EditorOpen), { initialValue: false });
  readonly editorLoading = toSignal(this.store.select(selectS3EditorLoading), {
    initialValue: false,
  });
  readonly editorFileName = toSignal(this.store.select(selectS3EditorFileName), {
    initialValue: "",
  });
  readonly editorFilePath = toSignal(this.store.select(selectS3EditorFilePath), {
    initialValue: "",
  });
  readonly uploadLoading = toSignal(this.store.select(selectS3UploadLoading), {
    initialValue: false,
  });
  readonly uploadFileName = toSignal(this.store.select(selectS3UploadFileName), {
    initialValue: "",
  });
  readonly uploadBusy = computed(() => this.readingUploadFile || this.uploadLoading());
  readonly canWriteInstances = this.auth.canWriteInstances;
  readonly uploadStatusLabel = computed(() => {
    if (this.readingUploadFile) {
      return `Reading ${this.readingUploadFileName || "file"}...`;
    }
    if (this.uploadLoading()) {
      return `Uploading ${this.uploadFileName() || "file"}...`;
    }
    return "";
  });

  // Local UI state
  query = "";
  readingUploadFile = false;
  readingUploadFileName = "";
  editorContent = "";
  editorOptions = {
    theme: "vs-dark",
    language: "plaintext",
    readOnly: false,
    automaticLayout: true,
    minimap: { enabled: false },
  };
  private editorInstance: Monaco.editor.IStandaloneCodeEditor | null = null;
  private readonly knownFolderPathsSet = new Set<string>(["/"]);

  get filteredEntries() {
    const q = this.query.trim().toLowerCase();
    const currentPath = this.currentPath();
    return this.entries().filter((entry) => {
      if (entry.path !== currentPath) {
        return false;
      }
      if (!q) {
        return true;
      }
      return entry.name.toLowerCase().includes(q);
    });
  }

  get breadcrumbs() {
    const parts = this.currentPath().split("/").filter(Boolean);
    const crumbs = [{ label: "root", path: "/" }];
    let acc = "";
    for (const part of parts) {
      acc += `/${part}`;
      crumbs.push({ label: part, path: acc });
    }
    return crumbs;
  }

  get treeNodes() {
    return this.knownFolderPaths()
      .filter((path) => path !== "/")
      .sort()
      .map((path) => {
        const parts = path.split("/").filter(Boolean);
        return {
          label: parts[parts.length - 1],
          path,
          level: parts.length - 1,
        };
      });
  }

  constructor() {
    this.actions$
      .pipe(ofType(s3EditorContentLoaded), takeUntilDestroyed())
      .subscribe(({ content }) => {
        this.editorContent = content;
        this.editorInstance?.setValue(content);
      });

    this.actions$
      .pipe(ofType(s3EditorContentLoadFailed), takeUntilDestroyed())
      .subscribe(({ filePath }) => {
        const errorContent = `Failed to load file content for ${filePath}.`;
        this.editorContent = errorContent;
        this.editorInstance?.setValue(errorContent);
      });

    this.actions$
      .pipe(ofType(s3EditorClosed, s3EditorSaveSucceeded), takeUntilDestroyed())
      .subscribe(() => {
        this.editorContent = "";
      });
  }

  ngOnInit(): void {
    if (!this.hasValidId()) {
      return;
    }
    this.store.dispatch(s3PageOpened({ instanceId: (this.instanceId() ?? "").trim() }));
  }

  isActivePath(path: string): boolean {
    return this.currentPath() === path;
  }

  isEditableEntry(entry: BrowserEntry): boolean {
    if (!entry || entry.type !== "file") {
      return false;
    }
    const name = `${entry.name ?? ""}`.trim().toLowerCase();
    return name.endsWith(".py") || name.endsWith(".pbtxt");
  }

  openEntry(entry: BrowserEntry) {
    if (entry.type === "folder") {
      const nextPath = this.joinPath(this.currentPath(), entry.name);
      const instanceId = (this.instanceId() ?? "").trim();
      if (instanceId) {
        this.store.dispatch(s3NavigateTo({ instanceId, path: nextPath }));
      }
      return;
    }

    if (entry.type === "file" && this.canEditEntry(entry)) {
      this.startEdit(entry);
    }
  }

  startEdit(entry: BrowserEntry): void {
    if (entry.type !== "file" || !this.canEditEntry(entry)) {
      return;
    }
    const instanceId = (this.instanceId() ?? "").trim();
    if (!instanceId) {
      return;
    }
    const filePath = this.joinPath(this.currentPath(), entry.name);
    this.editorContent = "";
    this.editorOptions = {
      ...this.editorOptions,
      language: this.detectLanguage(entry.name),
    };
    this.store.dispatch(s3EditorOpenRequested({ instanceId, filePath, fileName: entry.name }));
  }

  closeEditor(): void {
    this.store.dispatch(s3EditorClosed());
  }

  saveAndCloseEditor(): void {
    const instanceId = (this.instanceId() ?? "").trim();
    const filePath = this.editorFilePath();
    if (!instanceId || !filePath || !this.canWriteInstances()) {
      return;
    }

    if (this.isConfigPbtxtFile(filePath)) {
      const syntaxError = this.getPbtxtSyntaxError();
      if (syntaxError) {
        this.store.dispatch(displayFailure({ title: "Syntax error", message: syntaxError }));
        return;
      }
    }

    this.store.dispatch(
      s3EditorSaveRequested({ instanceId, filePath, content: this.editorContent }),
    );
  }

  goTo(path: string): void {
    const instanceId = (this.instanceId() ?? "").trim();
    if (instanceId) {
      this.store.dispatch(s3NavigateTo({ instanceId, path }));
    }
  }

  goUp(): void {
    if (this.currentPath() === "/") {
      return;
    }
    const parts = this.currentPath().split("/").filter(Boolean);
    parts.pop();
    const nextPath = parts.length ? `/${parts.join("/")}` : "/";
    this.goTo(nextPath);
  }

  onUpload(event: Event): void {
    const input = event.target as HTMLInputElement | null;
    if (!input?.files?.length) {
      return;
    }
    if (!this.canWriteInstances() || this.uploadBusy()) {
      input.value = "";
      return;
    }
    const instanceId = (this.instanceId() ?? "").trim();
    if (!instanceId) {
      input.value = "";
      return;
    }

    const file = input.files[0];
    const filePath = this.joinPath(this.currentPath(), file.name);
    const currentPath = this.currentPath();
    this.readingUploadFile = true;
    this.readingUploadFileName = file.name;

    void file
      .arrayBuffer()
      .then((buffer) => {
        this.store.dispatch(
          s3FileUploadRequested({
            instanceId,
            filePath,
            content: buffer,
            contentType: file.type || "application/octet-stream",
            fileName: file.name,
            currentPath,
          }),
        );
      })
      .catch((error: unknown) => {
        const message =
          error instanceof Error && error.message
            ? error.message
            : `Failed to read file ${file.name}.`;
        this.store.dispatch(displayFailure({ title: "Upload failed", message }));
      })
      .finally(() => {
        this.readingUploadFile = false;
        this.readingUploadFileName = "";
        input.value = "";
      });
  }

  canEditEntry(entry: BrowserEntry): boolean {
    return this.canWriteInstances() && this.isEditableEntry(entry);
  }

  private isConfigPbtxtFile(filePath: string): boolean {
    return filePath.trim().toLowerCase().endsWith("/config.pbtxt");
  }

  onEditorInit(editor: Monaco.editor.IStandaloneCodeEditor): void {
    this.editorInstance = editor;
    if (typeof this.editorContent === "string" && this.editorInstance?.setValue) {
      this.editorInstance.setValue(this.editorContent);
    }
  }

  private getMonacoSyntaxError(): string | null {
    const monacoApi = (globalThis as typeof globalThis & { monaco?: typeof Monaco })?.monaco;
    const model = this.editorInstance?.getModel?.();

    if (!monacoApi?.editor || !model) {
      return null;
    }

    const markers = monacoApi.editor.getModelMarkers({ resource: model.uri }) ?? [];
    const errorSeverity = monacoApi.MarkerSeverity?.Error ?? 8;
    const firstError = markers.find(
      (marker: Monaco.editor.IMarker) => marker?.severity === errorSeverity,
    );

    if (!firstError) {
      return null;
    }

    const line = firstError.startLineNumber;
    const column = firstError.startColumn;
    const message = `${firstError.message ?? ""}`.trim();

    if (message) {
      return `Syntaxfehler in config.pbtxt (Zeile ${line}, Spalte ${column}): ${message}`;
    }

    return `Syntaxfehler in config.pbtxt (Zeile ${line}, Spalte ${column}). Bitte rote Marker korrigieren.`;
  }

  private getPbtxtSyntaxError(): string | null {
    const monacoError = this.getMonacoSyntaxError();
    if (monacoError) {
      return monacoError;
    }
    return this.getBracketFallbackError(this.editorContent);
  }

  private getBracketFallbackError(text: string): string | null {
    const stack: Array<{ char: "{" | "[" | "("; line: number; col: number }> = [];
    const matching: Record<string, "{" | "[" | "("> = {
      "}": "{",
      "]": "[",
      ")": "(",
    };
    const expectedCloser: Record<"{" | "[" | "(", string> = {
      "{": "}",
      "[": "]",
      "(": ")",
    };

    let line = 1;
    let col = 0;
    let inString = false;
    let quoteChar = "";
    let escaped = false;

    for (let index = 0; index < text.length; index++) {
      const char = text[index];

      if (char === "\n") {
        line += 1;
        col = 0;
      } else {
        col += 1;
      }

      if (inString) {
        if (escaped) {
          escaped = false;
          continue;
        }
        if (char === "\\") {
          escaped = true;
          continue;
        }
        if (char === quoteChar) {
          inString = false;
          quoteChar = "";
        }
        continue;
      }

      if (char === "#") {
        while (index + 1 < text.length && text[index + 1] !== "\n") {
          index += 1;
        }
        continue;
      }

      if (char === '"' || char === "'") {
        inString = true;
        quoteChar = char;
        continue;
      }

      if (char === "{" || char === "[" || char === "(") {
        stack.push({ char: char as "{" | "[" | "(", line, col });
        continue;
      }

      if (char in matching) {
        const top = stack.pop();
        if (!top) {
          return `Syntaxfehler in config.pbtxt (Zeile ${line}, Spalte ${col}): Unerwartetes '${char}'.`;
        }
        if (top.char !== matching[char]) {
          return `Syntaxfehler in config.pbtxt (Zeile ${line}, Spalte ${col}): Erwartet '${expectedCloser[top.char]}', gefunden '${char}'.`;
        }
      }
    }

    if (inString) {
      return "Syntaxfehler in config.pbtxt: Nicht geschlossene Zeichenkette.";
    }

    const unclosed = stack.pop();
    if (unclosed) {
      return `Syntaxfehler in config.pbtxt (Zeile ${unclosed.line}, Spalte ${unclosed.col}): Fehlendes '${expectedCloser[unclosed.char]}'.`;
    }

    return null;
  }

  async download(entry: BrowserEntry): Promise<void> {
    if (entry.type !== "file") {
      return;
    }
    const instanceId = (this.instanceId() ?? "").trim();
    if (!instanceId) {
      return;
    }

    const filePath = this.joinPath(this.currentPath(), entry.name);
    try {
      const response = (await firstValueFrom(
        this.instancesApi.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet(
          instanceId,
          filePath,
          "response",
        ),
      )) as { body?: unknown };
      const body = response?.body;
      const blob =
        body instanceof Blob
          ? body
          : new Blob([typeof body === "string" ? body : ""], {
              type: "application/octet-stream",
            });
      this.saveBlob(entry.name, blob);
    } catch (error) {
      this.store.dispatch(
        displayFailure({
          title: "Download failed",
          message: mapApiErrorMessage(error, `Failed to download ${entry.name}.`),
        }),
      );
    }
  }

  private saveBlob(fileName: string, blob: Blob): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
  }

  private detectLanguage(fileName: string): string {
    const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
    const byExt: Record<string, string> = { py: "python", pbtxt: "plaintext" };
    return byExt[ext] ?? "plaintext";
  }

  private joinPath(basePath: string, name: string): string {
    const base = this.normalizePath(basePath);
    const cleanName = `${name ?? ""}`.replace(/^\/+|\/+$/g, "");
    if (!cleanName) {
      return base;
    }
    return this.normalizePath(`${base}/${cleanName}`);
  }

  private normalizePath(path: string | null | undefined): string {
    const clean = `${path ?? ""}`.replace(/\/+/g, "/").replace(/^\/+|\/+$/g, "");
    return clean ? `/${clean}` : "/";
  }
}
