# Minimalanforderungen an das `mlflow-triton-control`-Plugin

Der erste Ausbauschritt ist bewusst klein:

> Das Plugin erstellt über die Triton-Control-API ein Triton-Deployment mit
> einem konkreten Modell. Das Deployment verwendet ein Model Repository aus
> einem konfigurierten S3-Profil und S3-Präfix.


## Ziel

```bash
mlflow deployments create \
  -t triton-control://triton-control-api \
  --name iris-classifier \
  -m models:/iris-classifier/1
```

Der Ablauf:

```text
MLflow CLI/API
      │
      ▼
mlflow-triton-control Plugin
      │  Bearer Token
      ▼
Triton Control API
      │  S3-Profil + Repository-Präfix + Modellname
      ▼
Kubernetes Triton Deployment
      │
      ▼
S3 Model Repository
```

## Umfang

Im ersten Schritt:

- MLflow Deployment Target registrieren
- Triton-Control-URL konfigurieren
- Bearer-Token verwenden
- S3-Profil an Triton Control übergeben
- Repository-Präfix übergeben
- Triton-Deployment mit einem Modell erstellen
- `model-control-mode=explicit` setzen
- Deployment-Status abfragen
- Deployments auflisten und löschen

Noch nicht:

- MLflow Model URI in das Repository konvertieren oder hochladen
- sklearn- oder Transformers-Flavors erkennen
- `model.py` oder `config.pbtxt` erzeugen
- Modelle automatisch in das Repository kopieren
- `update_deployment()`
- `predict()` und Smoke-Tests
- MLflow Model Registry oder Artifact Store verwenden
- Modellversionen, Aliase oder Rollbacks verwalten

## Installation

Das Plugin wird im Argo- oder Deployment-Image installiert:

```text
mlflow-triton-control
mlflow>=3.14,<4
requests oder httpx
```

```toml
[project.entry-points."mlflow.deployments"]
triton-control = "mlflow_triton_control"
```

Der Target URI lautet:

```text
triton-control://triton-control-api
```

`tritonclient[http]` wird zunächst nicht benötigt, weil das Plugin noch
keine Inferenz ausführt.

## Konfiguration

Das Plugin benötigt:

```text
TRITON_CONTROL_URL
TRITON_CONTROL_TOKEN
TRITON_S3_PROFILE
TRITON_REPOSITORY_PREFIX
TRITON_IMAGE
```

Beispiel:

```text
TRITON_CONTROL_URL=http://triton-control-api:8000
TRITON_CONTROL_TOKEN=<bearer-token>
TRITON_S3_PROFILE=training-s3
TRITON_REPOSITORY_PREFIX=triton/development
TRITON_IMAGE=nvcr.io/nvidia/tritonserver:26.06-py3
TRITON_MODEL_NAME=iris_classifier
```

Alternativ werden Profil, Präfix und Modellname über die Deployment-Konfiguration
übergeben:

```python
from mlflow.deployments import get_deploy_client

client = get_deploy_client(
    "triton-control://triton-control-api"
)

client.create_deployment(
    name="iris-classifier",
    model_uri="models:/iris-classifier/1",
    flavor="triton",
    config={
        "s3_profile": "training-s3",
        "repository_prefix": "triton/development",
        "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
        "model_name": "iris_classifier",
    },
)
```

Das Plugin sendet:

```text
Authorization: Bearer <TRITON_CONTROL_TOKEN>
```

S3-Credentials werden nicht an das Plugin übergeben.

## Minimale MLflow Deployment API

Im ersten Schritt werden nur diese Funktionen implementiert:

| Funktion | Bedeutung |
|---|---|
| `create_deployment()` | Triton-Deployment mit vorhandenem Modell erstellen |
| `get_deployment()` | Deployment- und Readiness-Status abfragen |
| `list_deployments()` | vorhandene Deployments auflisten |
| `delete_deployment()` | Triton-Deployment entfernen |

Das Modell muss im angegebenen S3-Repository bereits in Triton-Struktur
vorliegen. Das Plugin übernimmt in dieser Version weder Konvertierung noch
Upload.

## Triton-Control-API

Das Plugin benötigt zunächst nur:

```text
POST /api/deployments
    Triton-Deployment mit S3-Profil, Repository-Präfix und Modell erstellen

GET /api/instances/{instance_id}
    Endpoint-Status lesen

DELETE /api/deployments/{instance_id}
    Triton-Deployment entfernen
```

Der Request soll minimal so aussehen:

```json
{
  "deployment_name": "development",
  "s3_profile": "training-s3",
  "repository_prefix": "triton/development",
  "model_name": "iris_classifier",
  "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
  "model_control_mode": "explicit"
}
```

Die aktuelle Deployment-API erwartet noch direkte S3-Credentials. Dafür muss
Triton Control serverseitig die Felder `s3_profile` und
`repository_prefix` akzeptieren und das Profil auflösen:

```text
s3_profile
    ▼
S3 Endpoint, Bucket und Credentials
    ▼
s3://<bucket>/<repository_prefix>
```

Das Plugin erhält niemals Access Keys oder Secret Keys.

## Triton-Deployment mit explizitem Modell

Das Deployment wird im Explicit-Mode gestartet. Der Modellname wird explizit
gesetzt und beim Start geladen:

```text
--model-control-mode=explicit
--load-model=iris_classifier
```

Das Modell muss unter dem konfigurierten Repository-Prefix vorhanden sein:

```text
s3://<bucket>/triton/development/iris_classifier/
```

Der Plugin-MVP prüft und provisioniert die Triton-Konfiguration, erzeugt aber
noch keine `config.pbtxt` oder `model.py`.

## Späterer Ausbau: Model Packaging

In einer späteren Version kann das Plugin zusätzlich ein MLflow-Modell
konvertieren und in das Repository hochladen:

```text
MLflow Model
      ▼
Model Packaging
      ▼
s3://<bucket>/triton/development/<model>/
      ▼
Triton Model Load
```

Das ist nicht Bestandteil der ersten Plugin-Version. In der ersten Version
muss die Triton-Struktur bereits im S3-Präfix vorhanden sein.

## Fehlerverhalten

Das Plugin muss:

- fehlende Konfiguration melden,
- ungültige S3-Profile melden,
- nicht erreichbares Triton Control melden,
- HTTP-Fehler verständlich weitergeben,
- auf die Readiness des Triton-Deployments warten,
- keine S3-Credentials loggen,
- bei einem bereits vorhandenen Endpoint idempotent reagieren.

## Minimaler Codeumfang

```text
mlflow_triton_control/
├── __init__.py
├── config.py
├── client.py
└── deployment_client.py
```

Der erste Client benötigt nur:

```python
class TritonControlDeploymentClient(BaseDeploymentClient):
    def create_deployment(self, name, model_uri, flavor=None, config=None):
        ...

    def get_deployment(self, name):
        ...

    def list_deployments(self):
        ...

    def delete_deployment(self, name):
        ...
```
