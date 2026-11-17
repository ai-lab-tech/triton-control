# Arbeitsregeln für Coding-Agents

Diese Regeln gelten für das gesamte Repository.

## Verbindliche lokale CI-Prüfung vor jedem Push

- Vor jedem Push die Workflows unter `.github/workflows/` lesen und anhand des
  gesamten zu pushenden Diffs bestimmen, welche Jobs betroffen sind. Nicht nur
  den letzten Commit oder einzelne geänderte Tests betrachten.
- Die betroffenen CI-Prüfungen zuerst lokal ausführen: gleiche Befehle,
  Arbeitsverzeichnisse, Tool-Versionen, Konfigurationen und Coverage-Grenzen wie
  im Workflow. Die Workflow-Dateien sind die maßgebliche Quelle.
- Nicht nach erfolgreichen Unit-Tests aufhören. Auch Typprüfung, Linter,
  Formatierung, Architekturprüfungen, Security-Scans, generierte Dateien und
  betroffene Integrations-/Smoke-Tests gehören zur Prüfung.
- Fehlende Werkzeuge und Abhängigkeiten nach Möglichkeit in einer isolierten
  Umgebung installieren; benötigte Testdienste lokal bereitstellen.
- Bei fehlgeschlagenen Prüfungen nicht pushen. Ursache beheben und betroffene
  Prüfungen erneut ausführen. Fehler nicht durch Abschwächen der CI-Regeln,
  Coverage-Grenzen oder Tests umgehen.
- Ist eine erforderliche Prüfung lokal nicht ausführbar, die konkrete Prüfung,
  Ursache und verbleibende Unsicherheit nennen. Alle übrigen Prüfungen fertig
  ausführen. Ohne ausdrückliche Freigabe für diese Ausnahme nicht pushen;
  eine allgemeine Aufforderung zum Push ist keine Freigabe zum Überspringen.
- Ergebnisse müssen zum endgültigen Code passen. Nach weiteren Codeänderungen
  die dadurch betroffenen Prüfungen wiederholen. Vor dem Push zusätzlich
  `git diff --check` ausführen und den zu pushenden Diff prüfen.
- Im Abschlussbericht bestandene, fehlgeschlagene und übersprungene Prüfungen
  getrennt nennen. Nur tatsächlich ausgeführte Prüfungen als bestanden melden.
  Lokaler Erfolg bedeutet nicht, dass GitHub CI bereits erfolgreich war.

## Backend

Bei Backend-Änderungen insbesondere den vollständigen `unit-tests`-Job aus
`.github/workflows/backend-ci.yml` lokal abdecken. Dazu gehören derzeit:

- Dependency-Audit und Prüfung der eingecheckten OpenAPI-Dateien
- vollständige Unit-Tests mit Coverage-Grenze und Coverage-Zusammenfassung
- `mypy app/ tests/ scripts/ main.py`
- `ruff check app/ main.py`
- `lint-imports`
- `bandit -r app/ main.py`

Je nach Änderung zusätzlich die betroffenen PostgreSQL-, S3-, Kubernetes-,
Helm-, Vertrags- und Extension-Tests aus den Workflows ausführen.

## Frontend

Bei Frontend-Änderungen `.github/workflows/frontend-ci.yml` beachten, insbesondere
Dependency-Audit, Security-Scan, API-Generierung, ESLint, Prettier, Unit-Tests mit
Coverage sowie betroffene Smoke-/End-to-End-Tests.

## Reine Dokumentationsänderungen

Wenn ausschließlich Dokumentation oder Agent-Anweisungen geändert werden und
kein Workflow für diese Dateien Prüfungen verlangt, sind keine fachfremden
Backend-/Frontend-Testläufe nötig. Workflow-Trigger und Verweise prüfen sowie
`git diff --check` ausführen. Diese Ausnahme gilt nicht für Codeänderungen,
die im selben Push enthalten sind.
