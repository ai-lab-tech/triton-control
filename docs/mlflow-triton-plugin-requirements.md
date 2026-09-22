# Minimalanforderungen an das `mlflow-triton-control`-Plugin

Der erste Ausbauschritt ist bewusst klein:

> Das Plugin erstellt über die Triton-Control-API einen leeren Triton-Endpoint,
> der ein Model Repository aus einem konfigurierten S3-Profil und S3-Präfix
> verwendet.

Das Plugin lädt in diesem Schritt noch keine MLflow-Modelle herunter und
erzeugt noch kein Triton-Modelldatei-Set.

## Ziel

```bash
mlflow deployments create-endpoint \
  -t triton-control://triton-control-api \
  --name development
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
      │  S3-Profil + Repository-Präfix
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
- leeren Triton-Endpoint erstellen
- Endpoint-Status abfragen
- Endpoints auflisten und löschen

Noch nicht:

- MLflow Model URI herunterladen
- sklearn- oder Transformers-Flavors erkennen
- `model.py` oder `config.pbtxt` erzeugen
- Modelle in das Repository kopieren
- `create_deployment()` für Modelle
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
```

Alternativ werden Profil und Präfix über die Endpoint-Konfiguration übergeben:

```python
from mlflow.deployments import get_deploy_client

client = get_deploy_client(
    "triton-control://triton-control-api"
)

client.create_endpoint(
    name="development",
    config={
        "s3_profile": "training-s3",
        "repository_prefix": "triton/development",
        "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
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
| `create_endpoint()` | leeren Triton-Endpoint erstellen |
| `get_endpoint()` | Endpoint- und Readiness-Status abfragen |
| `list_endpoints()` | vorhandene Endpoints auflisten |
| `delete_endpoint()` | Triton-Deployment entfernen |

Modellfunktionen bleiben zunächst nicht implementiert:

```python
def create_deployment(*args, **kwargs):
    raise NotImplementedError(
        "Model deployment is not implemented yet"
    )
```

## Triton-Control-API

Das Plugin benötigt zunächst nur:

```text
POST /api/deployments
    Triton-Deployment mit S3-Profil und Repository-Präfix erstellen

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

## Leerer Triton-Endpoint

Beim leeren Endpoint wird kein Modellname gesetzt. Triton darf deshalb nicht
mit `--load-model=*` starten. Das Backend muss den Parameter bei
fehlendem Modellnamen vollständig weglassen:

```text
--model-control-mode=explicit
```

Das Repository muss am Anfang noch kein Modell enthalten:

```text
s3://<bucket>/triton/development/
```

Ein physisches S3-Verzeichnis muss nicht angelegt werden.

## Späterer Ausbau

Der gleiche Endpoint kann später für Modell-Deployments verwendet werden:

```text
MLflow Model
      ▼
Model Packaging
      ▼
s3://<bucket>/triton/development/<model>/
      ▼
Triton Model Load
```

Das ist nicht Bestandteil der ersten Plugin-Version.

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
    def create_endpoint(self, name, config=None):
        ...

    def get_endpoint(self, endpoint):
        ...

    def list_endpoints(self):
        ...

    def delete_endpoint(self, endpoint):
        ...
```
