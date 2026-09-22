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
  -m models:/iris-classifier/1 \
  -C s3_profile_id=7 \
  -C repository_prefix=triton/development \
  -C model_name=iris_classifier \
  -C image=nvcr.io/nvidia/tritonserver:26.06-py3
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
      │  S3-Profil-ID + Repository-Präfix + Modellname
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
- ID eines vorhandenen S3-Profils an Triton Control übergeben
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

Der Target URI enthält die interne Adresse der Triton-Control-API. Im gleichen
Kubernetes-Namespace genügt der Service-Name. Der tatsächliche Name hängt vom
Helm-Release ab:

```text
triton-control://<kubernetes-service-name>
```

Nur der Zugriffstoken ist geheim und wird dem Argo-Pod über ein Kubernetes
Secret bereitgestellt:

```yaml
env:
  - name: TRITON_CONTROL_TOKEN
    valueFrom:
      secretKeyRef:
        name: mlflow-triton-control
        key: token
```

Die übrigen Werte sind normale Deployment-Konfiguration:

| Wert | Beispiel | Secret |
|---|---|---|
| Target URI | `triton-control://triton-control-api` | Nein |
| `s3_profile_id` | `7` | Nein |
| `repository_prefix` | `triton/development` | Nein |
| `model_name` | `iris_classifier` | Nein |
| `image` | `nvcr.io/nvidia/tritonserver:26.06-py3` | Nein |
| `TRITON_CONTROL_TOKEN` | Bearer-JWT | Ja |

Über die Python API werden Profil, Präfix und Modellname ebenfalls in der
Deployment-Konfiguration übergeben:

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
        "s3_profile_id": 7,
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

Das bereits für Argo vorhandene `artifactRepositoryRef` stellt dem
Argo-Executor S3-Zugangsdaten bereit. Das Plugin liest dieses Secret nicht.
Stattdessen löst Triton Control die übergebene `s3_profile_id`
serverseitig auf.

## Vorhandene Secret-Infrastruktur

Triton Control besitzt bereits die benötigten Mechanismen:

- S3-Profile werden benutzerbezogen in der Datenbank gespeichert.
- S3 Secret Keys werden mit `S3_SECRET_ENCRYPTION_KEY` verschlüsselt.
- Ein verknüpftes Argo-S3-Profil erzeugt eine Artifact-Repository-ConfigMap
  und ein Kubernetes Secret.
- Triton-Deployments erhalten bereits ein eigenes Kubernetes Secret mit
  `AWS_ACCESS_KEY_ID` und `AWS_SECRET_ACCESS_KEY`.

Diese Mechanismen werden wiederverwendet. Das Plugin erzeugt kein eigenes
S3-Secret und mountet auch nicht das Argo-Artifact-Secret.

Dass Argo und Triton Control im selben Namespace laufen, macht Secrets nicht
automatisch für jeden Pod verfügbar. Nur explizit referenzierte Secrets werden
als Umgebungsvariable oder Volume eingebunden.

## Authentifizierung des Argo-Pods

Die Triton-Control-API akzeptiert bereits Bearer-JWTs. Für den MVP wird der
Token über ein Kubernetes Secret in den Argo-Pod injiziert. Lokale JWTs laufen
standardmäßig nach 60 Minuten ab. Für dauerhaft automatisierte Workflows sollte
später ein eigener Service-Account- oder API-Token mit eingeschränkten Rechten
ergänzt werden.

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
  "deployment_name": "iris-classifier",
  "s3_profile_id": 7,
  "repository_prefix": "triton/development",
  "model_name": "iris_classifier",
  "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
  "model_control_mode": "explicit"
}
```

Die aktuelle Deployment-API erwartet noch `s3_url`,
`s3_access_key` und `s3_secret_key` direkt im Request.
Sie muss für das Plugin um `s3_profile_id` und
`repository_prefix` erweitert werden:

```text
s3_profile_id
    ▼
Benutzereigenes S3-Profil aus der Datenbank laden
    ▼
Secret Key serverseitig entschlüsseln
    ▼
Endpoint, Bucket, Region und Credentials auflösen
    ▼
s3://<bucket>/<repository_prefix>
    ▼
Deployment-spezifisches Kubernetes Secret erzeugen
```

Das Plugin erhält niemals Access Keys oder Secret Keys.

Bei der Profilauflösung muss Triton Control die bereits vorhandene
benutzerbezogene Zugriffskontrolle verwenden. Ein Benutzer darf nur seine
eigenen S3-Profile für ein Deployment auswählen.

Der `repository_prefix` ist der vollständige Objektpfad innerhalb des
Buckets. Ein im S3-Profil gespeicherter Browser-Prefix wird nicht automatisch
vorangestellt.

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
- fehlende oder nicht berechtigte S3-Profile melden,
- nicht erreichbares Triton Control melden,
- HTTP-Fehler verständlich weitergeben,
- auf die Readiness des Triton-Deployments warten,
- keine S3-Credentials loggen,
- bei einem bereits vorhandenen Deployment idempotent reagieren.

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
