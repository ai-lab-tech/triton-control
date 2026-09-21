# Anforderungen an das `mlflow-triton-control`-Plugin

Das Plugin ist ein MLflow Deployment Plugin für das Triton-Control-Deployment
Endpoint. Es verbindet MLflow Model Registry und Artifact Store mit einem
bereits vorhandenen oder neu angelegten Triton-Endpoint.

## Ziel-URI und Installation

Das Plugin wird als Python-Paket im Argo-Deployment-Image installiert:

```text
mlflow-triton-control
mlflow>=3.14,<4
requests oder httpx
tritonclient[http]
```

Es registriert einen MLflow Deployment Target:

```toml
[project.entry-points."mlflow.deployments"]
triton-control = "mlflow_triton_control"
```

Der Target URI lautet beispielsweise:

```text
triton-control://triton-control-api
```

Der Target URI enthält die Adresse der Triton-Control-API. Zugangsdaten und
Endpoint-Standardwerte kommen aus der Plugin-Konfiguration beziehungsweise aus
Kubernetes Secrets.

## Benötigte Plugin-Konfiguration

```text
TRITON_CONTROL_URL
TRITON_CONTROL_TOKEN
TRITON_ENDPOINT
TRITON_INSTANCE_ID              # optional, wenn Endpoint bereits bekannt ist
TRITON_MODEL_REPOSITORY_PREFIX  # Triton-S3-Präfix
MLFLOW_TRACKING_URI
```

Der Token wird als Bearer Token an Triton Control gesendet. S3-Zugangsdaten für
das Triton Repository sollen nicht im Plugin liegen. Triton Control soll die
konfigurierte S3-Verbindung der Instance verwenden.

## MLflow Deployment API

Das Plugin implementiert die Standardfunktionen von
`mlflow.deployments.BaseDeploymentClient`:

| MLflow-Funktion | Bedeutung im Plugin |
|---|---|
| `create_endpoint()` | Triton-Endpoint mit leerem Repository erstellen |
| `get_endpoint()` | Triton-Endpoint und Readiness abfragen |
| `list_endpoints()` | verfügbare Triton-Endpoints auflisten |
| `delete_endpoint()` | Triton-Deployment entfernen |
| `create_deployment()` | MLflow-Modell in Triton veröffentlichen und laden |
| `update_deployment()` | neue MLflow-Version veröffentlichen und laden |
| `get_deployment()` | Modell- und Triton-Status abfragen |
| `list_deployments()` | geladene Modelle auflisten |
| `delete_deployment()` | Modell entladen beziehungsweise entfernen |
| `predict()` | Inferenz über den Triton-Endpoint ausführen |

## Endpoint und Deployment sind getrennt

Ein Endpoint entspricht einem laufenden Triton-Server. Er wird normalerweise
einmalig erstellt:

```bash
mlflow deployments create-endpoint \
  -t triton-control://triton-control-api \
  --name development
```

Ein Deployment entspricht einem Modell innerhalb dieses Triton-Servers:

```bash
mlflow deployments create \
  -t triton-control://triton-control-api \
  --endpoint development \
  --name sentiment-classifier \
  -m models:/sentiment-classifier/4
```

Der Endpoint muss bereits mit einem kompatiblen Triton-Image und den benötigten
Python-Abhängigkeiten laufen. Ein leeres Repository darf nicht mit
`--load-model=*` gestartet werden. Bei fehlendem Modellnamen muss Triton ohne
`--load-model` starten.

## Modell-Deployment-Verhalten

`create_deployment()` führt diese Schritte aus:

1. MLflow Model URI auflösen, zum Beispiel `models:/sentiment-classifier/4`.
2. MLflow-Modell aus dem MLflow Artifact Store herunterladen.
3. Die `MLmodel`-Datei und den Flavor auswerten.
4. Den passenden Adapter auswählen:
   - `sklearn` → sklearn Python-Backend-Template
   - `transformers` → Hugging-Face Python-Backend-Template
   - `triton` → vorhandenes Repository übernehmen
5. `config.pbtxt` und `model.py` erzeugen.
6. Modellartefakte und Tokenizer in die Triton-Struktur kopieren.
7. Alle Dateien in ein versioniertes Triton-S3-Präfix hochladen.
8. Triton über die Repository API laden.
9. Auf `READY` warten.
10. Den Deploymentstatus zurückgeben.

Für einen Upload soll zuerst in ein temporäres Präfix geschrieben werden. Die
Konfiguration beziehungsweise der abschließende Publish-Schritt wird zuletzt
geschrieben. So sieht Triton kein unvollständiges Modell.

## Triton-Control-API-Aufrufe

Das Plugin verwendet die Triton-Control-API und schreibt nicht direkt in die
Kubernetes- oder Triton-Control-Datenbank:

```text
POST /api/deployments
    Triton-Deployment beziehungsweise Endpoint erstellen

GET  /api/instances/{instance_id}/models
    Repository- und Modellstatus lesen

PUT  /api/instances/{instance_id}/s3/content?path=...
    eine Repository-Datei hochladen

POST /api/instances/{instance_id}/models/{model_name}/load
    Modell explizit laden

POST /api/instances/{instance_id}/models/{model_name}/unload
    Modell explizit entladen

POST /api/instances/{instance_id}/models/{model_name}/versions/{version}/infer
    Smoke-Test beziehungsweise Inferenz proxyen
```

Der bestehende Upload-Endpunkt ist für kleine Modelle ausreichend. Für große
Transformer- oder LLM-Artefakte sollte später ein atomarer Multipart- oder
serverseitiger Import-Endpunkt ergänzt werden.

## Flavors und Abhängigkeiten

Das Plugin unterstützt zunächst:

```text
sklearn
transformers
triton
```

Die MLflow-Abhängigkeiten werden nicht automatisch in einen laufenden Triton-
Pod installiert. Sie müssen bereits im Triton-Image, über `requirements_txt`
oder über eine kompatible Python Execution Environment vorhanden sein.

Beispiel für einen Python-Backend-Endpoint:

```text
scikit-learn==1.5.2
joblib==1.4.2
torch
transformers
safetensors
tokenizers
```

Bei unterschiedlichen Python-Umgebungen pro Modell muss die Python-Version zur
Triton-Python-Backend-Stub-Version passen.

## Versionierung, Update und Rollback

Die MLflow-Modellversion wird zur Triton-Modellversion:

```text
models:/sentiment-classifier/4
                    │
                    └── Triton: sentiment_classifier/4/
```

Beim Update wird die neue Version separat hochgeladen und anschließend geladen.
Die vorherige Version bleibt für Rollback verfügbar:

```bash
mlflow deployments update \
  -t triton-control://triton-control-api \
  --endpoint development \
  --name sentiment-classifier \
  -m models:/sentiment-classifier/3
```

Das Plugin schreibt zusätzlich Deployment-Tags in MLflow:

```text
deployment_status=deployed|failed
triton_endpoint=development
triton_model_name=sentiment_classifier
triton_model_version=4
```

Der Alias `champion` wird erst nach einem erfolgreichen Smoke-Test gesetzt.

## Fehlerverhalten und Idempotenz

Das Plugin muss:

- bei einem fehlenden MLflow Model mit einer verständlichen Fehlermeldung
  abbrechen,
- unbekannte Flavors oder Tasks ablehnen,
- inkompatible Python-Abhängigkeiten vor dem Upload melden,
- bei einem erneuten Deployment derselben Version idempotent sein,
- ein unvollständiges Triton-Modell nicht laden,
- bei einem fehlgeschlagenen Load keinen `champion`-Alias setzen,
- den Triton- und MLflow-Status im Fehlerfall synchronisieren.

Der gewünschte Standard ist daher:

```text
MLflow Model URI
      │
      ▼
mlflow-triton-control Plugin
      │
      ├── native MLflow Artifact Store lesen
      ├── Triton Repository erzeugen
      ├── Triton-Control-API verwenden
      ├── Triton Model Load ausführen
      └── Deploymentstatus in MLflow schreiben
```
