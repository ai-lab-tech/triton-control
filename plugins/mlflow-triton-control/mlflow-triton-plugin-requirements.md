# Minimalanforderungen an das `mlflow-triton-control`-Plugin

Der erste Ausbauschritt ist bewusst klein:

> Das Plugin erstellt über die Triton-Control-API ein Triton-Deployment mit
> einem konkreten Modell. Das Deployment verwendet ein Model Repository aus
> einem konfigurierten S3-Profil und S3-Präfix.

Das Plugin ist ein eigenständiges Python-Paket mit einem MLflow-Deployment-Entry-Point.
Es erweitert die installierte MLflow-Umgebung, ohne den MLflow-Quellcode oder
den Tracking-Server zu verändern. Ein Container-Image mit installiertem Paket
ist später die reproduzierbare Laufzeit für den Argo-Deployment-Schritt;
das Triton-Server-Image ist davon getrennt.

## Ziel

```bash
mlflow deployments create \
  -t triton-control://triton-control-api \
  --name iris-classifier \
  -m s3://triton-models/triton/development/iris_classifier \
  -C s3_profile_id=7 \
  -C image=nvcr.io/nvidia/tritonserver:26.06-py3
```

`-m` bezeichnet in dieser Version **das bereits vorhandene Triton-Modell**:
`s3://<bucket>/<repository_prefix>/<model_name>`. Das ist keine MLflow
Registry-URI. Das Plugin zerlegt die URI in Bucket, Repository-Präfix und
Modellname; der S3-Bucket muss mit dem ausgewählten Profil übereinstimmen.
Triton erhält den Repository-Pfad **eine Ebene über dem Modellordner**. Eine
`models:/...`- oder `runs:/...`-URI wird im MVP mit einer klaren Fehlermeldung
abgelehnt, weil noch keine MLflow-Artefakte heruntergeladen werden.

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
- Bucket, Repository-Präfix und Modellname aus `model_uri` ableiten
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

## Paket und Installation

Das Paket wird zunächst lokal als Wheel gebaut und in derselben Python-Umgebung
wie der aufrufende `mlflow`-CLI-Befehl installiert. Anschließend wird genau
dieses Paket im Argo- oder Deployment-Image installiert. Es muss nicht im
MLflow-Tracking-Server installiert werden.

```bash
python -m build
python -m pip install dist/mlflow_triton_control-*.whl
mlflow deployments help -t triton-control
```

Paket-Abhängigkeiten: eine gegen die tatsächlich verwendete MLflow-Version
getestete Versionsspanne und ein HTTP-Client, beispielsweise `httpx`. Die
konkreten Versionsgrenzen werden beim Paketbau festgelegt.

```toml
[project.entry-points."mlflow.deployments"]
triton-control = "mlflow_triton_control.deployment_client"
```

Das Zielmodul enthält genau eine `BaseDeploymentClient`-Unterklasse sowie
`target_help()` und `run_local()`. `run_local()` erklärt für den MVP, dass
lokales Triton-Serving nicht unterstützt wird.

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

Für einen lokalen Test kann der Benutzer dem CLI-Prozess einen eigenen,
gültigen Bearer-Token als `TRITON_CONTROL_TOKEN` bereitstellen. Bei Argo muss
Triton Control die Identität des angemeldeten Workflow-Starters an den
Deployment-Schritt delegieren. Ein dauerhaft im Namespace hinterlegter
Benutzer-JWT ist dafür nicht vorgesehen. Die konkrete Ausgabe und Übergabe
des kurzlebigen Workflow-Tokens ist eine erforderliche Backend- und
Workflow-Erweiterung (siehe „Authentifizierung des Argo-Pods“).

Der Deployment-Pod erhält den Token aus einem nur für diesen Workflow
bereitgestellten Kubernetes Secret:

```yaml
env:
  - name: TRITON_CONTROL_TOKEN
    valueFrom:
      secretKeyRef:
        name: <workflow-spezifisches-token-secret>
        key: token
```

Die übrigen Werte sind normale Deployment-Konfiguration:

| Wert | Beispiel | Secret |
|---|---|---|
| Target URI | `triton-control://triton-control-api` | Nein |
| `s3_profile_id` | `7` | Nein |
| `model_uri` | `s3://triton-models/triton/development/iris_classifier` | Nein |
| `image` | `nvcr.io/nvidia/tritonserver:26.06-py3` | Nein |
| `TRITON_CONTROL_TOKEN` | Kurzlebiger, benutzergebundener Workflow-Token | Ja |

Über die Python API werden S3-Profil und Image in der Deployment-Konfiguration
übergeben. Präfix und Modellname stammen aus `model_uri`:

```python
from mlflow.deployments import get_deploy_client

client = get_deploy_client(
    "triton-control://triton-control-api"
)

client.create_deployment(
    name="iris-classifier",
    model_uri="s3://triton-models/triton/development/iris_classifier",
    config={
        "s3_profile_id": 7,
        "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
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

Die code-server-Extension ist die Referenz für die **vorhandenen API-Pfade und
Benutzerrechte**: Ihr Webview ruft `POST /api/deployments` und
`GET /api/s3-profiles` mit der Browser-Session (`credentials: 'include'`) auf.
Ein Python-Prozess im Argo-Pod besitzt keine Browser-Session. Er verwendet
einen für den Workflow delegierten Bearer-Token, der bei `get_claims` wieder
denselben Benutzer ergeben muss. Beide Wege müssen dadurch dieselben
Zugriffsregeln für Deployment und S3-Profil anwenden.

Die Extension lädt Profilwerte für ihren Upload und sendet heute S3-Credentials
im Deployment-Request. Das Plugin **übernimmt diesen Credential-Transport
nicht**: Es sendet nur die Profil-ID. Die neue Profilauflösung findet in der
Triton-Control-API statt. Das Plugin ruft `GET /api/s3-profiles` nicht ab, weil
dessen aktuelle Antwort auch entschlüsselte Zugangsdaten enthält.

## Vorhandene Secret-Infrastruktur

Triton Control besitzt bereits die benötigten Mechanismen:

- S3-Profile werden benutzerbezogen in der Datenbank gespeichert.
- S3 Secret Keys werden im Profil gespeichert. Die aktuellen
  `encrypt_secret()`/`decrypt_secret()`-Funktionen sind noch Platzhalter ohne
  Verschlüsselung; echte Verschlüsselung ruhender Secrets ist vor produktiver
  Nutzung dieser Zugangsdaten gesondert umzusetzen.
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

Die Extension authentifiziert einen interaktiven API-Aufruf mit der
Triton-Control-Browser-Session. Der Argo-Pod kann diese Cookie-Session nicht
selbst übernehmen. Damit er trotzdem **als derselbe Benutzer** handelt, muss
Triton Control beim authentifizierten Start eines Workflows einen kurzlebigen,
an Benutzer und Workflow gebundenen Berechtigungsnachweis ausstellen und dem
Deployment-Pod zugänglich machen. Die Implementierung ergänzt diese Delegation
am authentifizierten Triton-Control-Argo-Proxy.

Anforderungen an die Übergabe:

1. Triton Control prüft beim Workflow-Start die Browser-Session beziehungsweise
   den Bearer-Token des Starters und speichert die zugehörige Benutzer-ID.
2. Ein Token für den Workflow und die benötigten Deployment-API-Aufrufe wird
   mit kurzer Laufzeit ausgestellt. Die API akzeptiert ihn nur für
   Create/Get/List/Delete des festgelegten Deployment-Namens und für die
   festgelegte S3-Profil-ID. Die Workflow-Zuordnung erfolgt über das temporäre
   Secret und dessen Kubernetes-Owner-Referenz. Der Token wird nicht als
   langlebiges Secret für alle Argo-Pods hinterlegt und nicht in
   Workflow-Parametern, Artefakten oder Logs ausgegeben.
3. Nur der betreffende Deployment-Pod erhält den Token, beispielsweise über ein
   workflow-spezifisches Kubernetes Secret. Triton Control entfernt ihn nach
   Workflow-Ende. Die Gültigkeitsdauer muss lange genug für den
   Deployment-Schritt sein; Ablauf oder Entzug führt zu einem klaren 401/403.
4. Die Triton-Control-API ordnet den Token wieder dem ursprünglichen Benutzer
   zu und prüft dessen aktuelle Freigabe und Profil-Eigentümerschaft bei jedem
   Aufruf. Eine frei angegebene Benutzer-ID oder `s3_profile_id` im Request
   ersetzt diese Prüfung nicht.

Lokale Login-JWTs laufen standardmäßig nach 60 Minuten ab. Sie sind für
lokale Tests geeignet, aber kein dauerhaft im Cluster gespeicherter Ersatz
für die beschriebene Delegation. Falls Workflows ausschließlich direkt über
die bestehende Argo-UI gestartet werden, muss zuerst ein Triton-Control-
vermittelter Startpfad verwendet werden. Der Argo-Proxy reicht Browser-Cookies
nicht an Argo weiter, sondern erzeugt bei entsprechend markierten Workflows
den eingeschränkten Token selbst.

Die Workflow-Annotationen `triton-control.ai/mlflow-deploy-template`,
`triton-control.ai/mlflow-deployment-name` und
`triton-control.ai/mlflow-s3-profile-name` aktivieren die Delegation. Der Proxy
löst den Profilnamen für den angemeldeten Benutzer auf, erstellt das kurzlebige
Secret und injiziert den Token sowie `TRITON_CONTROL_S3_PROFILE_ID` in das
benannte Pod-Template. Die bisherige ID-Annotation bleibt kompatibel. Details stehen in der
[Paket-README](README.md#argo-workflows).

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

`create_deployment()` folgt der MLflow-Signatur einschließlich optionalem
`endpoint`. Ein übergebener `endpoint` oder ein nicht unterstützter `flavor`
wird explizit abgelehnt. Die Rückgabe enthält mindestens `name` und die
Triton-Control-`instance_id`. `target_help()` dokumentiert URI-Format,
Konfigurationsfelder und die Abgrenzung zu `models:/`-URIs.

## Triton-Control-API

Das Plugin benötigt zunächst nur:

```text
POST /api/deployments
    Triton-Deployment mit S3-Profil, Repository-Präfix und Modell erstellen

GET /api/deployments/mlflow/{deployment_name}
    Deployment-Status und Readiness des konkreten Modells lesen

GET /api/deployments/mlflow
    Eigene, durch das Plugin angelegte Deployments auflisten

DELETE /api/deployments/mlflow/{deployment_name}
    Eigenes Triton-Deployment anhand des MLflow-Namens entfernen
```

Der Request soll minimal so aussehen:

```json
{
  "deployment_name": "iris-classifier",
  "s3_profile_id": 7,
  "model_uri": "s3://triton-models/triton/development/iris_classifier",
  "repository_prefix": "triton/development",
  "model_name": "iris_classifier",
  "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
  "model_control_mode": "explicit"
}
```

Die aktuelle Deployment-API erwartet noch `s3_url`,
`s3_access_key` und `s3_secret_key` direkt im Request. Sie muss für das Plugin
eine **alternative** Request-Form mit `s3_profile_id` und
`model_uri`, `repository_prefix` und `model_name` akzeptieren. Das Backend
prüft, dass Bucket, Präfix und Modellname in diesen Feldern übereinstimmen.
Die bisherige Form bleibt für die
code-server-Extension und andere bestehende Aufrufer erhalten; ein Request
darf die beiden Formen nicht mischen.

```text
s3_profile_id
    ▼
Benutzereigenes S3-Profil aus der Datenbank laden
    ▼
S3-Zugangsdaten serverseitig lesen (später entschlüsseln)
    ▼
Endpoint, Bucket, Region und Credentials auflösen
    ▼
s3://<bucket>/<repository_prefix>
    ▼
Deployment-spezifisches Kubernetes Secret erzeugen
```

Das Plugin erhält niemals Access Keys oder Secret Keys.

Bei der Profilauflösung muss Triton Control die bereits vorhandene
benutzerbezogene Zugriffskontrolle verwenden: `get_claims`,
`require_user_entity` und `s3_profiles.find_for_owner`. Ein Benutzer darf nur
seine eigenen S3-Profile für ein Deployment auswählen. Ein fremdes Profil
wird wie ein nicht vorhandenes Profil behandelt. Das Backend übernimmt aus dem
Profil Endpoint, Bucket, Region, Pfadstil, CA-Zertifikat und Zugangsdaten,
prüft den Bucket gegen die Modell-URI und erzeugt das bestehende
deployment-spezifische Kubernetes Secret.

Der `repository_prefix` ist der vollständige Objektpfad innerhalb des
Buckets. Ein im S3-Profil gespeicherter Browser-Prefix wird nicht automatisch
vorangestellt.

Die bestehende `POST`-Antwort enthält eine `instance_id`; angelegte
Kubernetes-Ressourcen bedeuten noch nicht, dass das Modell bereit ist. Das
Plugin fragt den Status mit einem begrenzten Timeout ab. Der Status muss auch
die Readiness des **konkreten Modells** abbilden; reine Pod- oder
Server-Readiness genügt nicht. Falls die bestehende Instanzantwort das nicht
leistet, wird ein passender Status-Endpunkt ergänzt. Für `get`, `list`
und `delete` muss die API die MLflow-Deployment-Namen eindeutig den von diesem
Plugin angelegten `instance_id`s zuordnen können. Die `GET /api/instances`-Liste
enthält auch andere Triton-Instanzen und darf nicht ungefiltert als MLflow-
Deployment-Liste ausgegeben werden. Wenn der vorhandene Instanzdatensatz keine
stabile Herkunftsmarkierung bietet, wird diese im Backend ergänzt.

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
- bei einem bereits vorhandenen Deployment einen verständlichen Konfliktfehler
  melden; `create` ersetzt oder löscht kein vorhandenes Deployment.

## Minimaler Codeumfang

```text
mlflow_triton_control/
├── __init__.py
└── deployment_client.py
```

Der erste Client benötigt nur:

```python
class TritonControlDeploymentClient(BaseDeploymentClient):
    def create_deployment(self, name, model_uri, flavor=None, config=None, endpoint=None):
        ...

    def get_deployment(self, name):
        ...

    def list_deployments(self):
        ...

    def delete_deployment(self, name):
        ...
```

## Abnahme und Quellen

- Das lokal gebaute Wheel lässt sich mit MLflow installieren;
  `mlflow deployments help -t triton-control` lädt den Entry Point.
- Ein gültiger Benutzer-Token kann ein Modell aus seinem eigenen S3-Profil
  deployen; ein fremdes Profil wird auch dann abgelehnt, wenn dessen ID bekannt
  ist. Die bestehende Browser-Session der code-server-Extension funktioniert
  mit der bisherigen Request-Form weiterhin.
- `model_uri` mit `models:/...`, falschem Bucket oder fehlendem Modellordner
  wird verständlich abgelehnt. Create/Get/List/Delete nutzen die stabile
  Zuordnung zwischen MLflow-Deployment und Triton-Control-Instanz.

Referenzen: [MLflow-Plugin-Schnittstelle](https://mlflow.org/docs/latest/ml/plugins),
[MLflow-Deployment-API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.deployments.html),
[NVIDIA `mlflow-triton`](https://catalog.ngc.nvidia.com/orgs/nvidia/morpheus/containers/mlflow-triton-plugin/-),
[code-server-Extension](../../code-server-extensions/triton-deploy/README.md).
