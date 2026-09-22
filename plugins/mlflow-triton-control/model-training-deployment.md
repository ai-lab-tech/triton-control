# Model Training, Registrierung und Triton Deployment

Diese Seite beschreibt den einfachsten vorgesehenen Ablauf für Data Scientists:
Ein Argo Workflow trainiert und registriert ein Modell mit MLflow, das
`mlflow-triton-control`-Plugin verpackt es für Triton und ein abschließender
Smoke-Test prüft die echte Inferenz.

Der MLflow Artifact Store bleibt dabei auf dem persistenten Volume der
verwalteten MLflow-Installation. MLflow-Modellartefakte werden nicht in einem
separaten S3 Artifact Store gespeichert. S3 wird nur für das ausführbare Triton
Model Repository verwendet.

## Zielbild

```text
Data Scientist
      │
      │ Argo Workflow starten
      ▼
┌────────────────────────────────────────────────────────────┐
│ Argo Workflow                                              │
│                                                            │
│  1. TRAIN + REGISTER                                       │
│     [MLflow Standard – kein eigenes Plugin]                 │
│                                                            │
│     ├── Modell trainieren                                  │
│     ├── Parameter und Metriken nach MLflow                 │
│     ├── MLflow Model erzeugen                              │
│     ├── Modell im MLflow Artifact Store speichern          │
│     └── Registry-Version erzeugen                          │
│                                                            │
│     Ergebnis: models:/<name>/<version>                      │
│                         │                                  │
│                         ▼                                  │
│  2. DEPLOY                                                 │
│     [MLflow + eigenes mlflow-triton-control Plugin]         │
│                                                            │
│     ├── MLflow Model herunterladen                         │
│     ├── Flavor und Task erkennen                           │
│     ├── Triton Python Repository erzeugen                  │
│     ├── Repository nach Triton-S3 hochladen                │
│     └── Triton Model Load                                  │
│                         │                                  │
│                         ▼                                  │
│  3. SMOKE TEST                                             │
│     [MLflow-Plugin + Triton]                                │
│                                                            │
│     ├── echte Inferenz durchführen                         │
│     ├── deployment_status setzen                           │
│     └── champion-Alias setzen                              │
└────────────────────────────────────────────────────────────┘
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
             erfolgreich          fehlgeschlagen
                 │                   │
                 ▼                   ▼
       deployment_status=       deployment_status=
       deployed                 failed
       alias=champion           kein champion-Alias
```

## Verantwortlichkeiten

| Schritt | Argo | MLflow Standard | Eigenes MLflow-Plugin | Triton |
|---|---:|---:|---:|---:|
| Training und Tracking | Ja | Ja | Nein | Nein |
| Modellregistrierung | Ja | Ja | Nein | Nein |
| Repository-Erzeugung | Ja | Modell-Download | Ja | Nein |
| Upload nach Triton-S3 | Ja | Nein | Ja | Nein |
| Model Load | Ja | Nein | Ja | Ja |
| Smoke-Test | Ja | Status und Tags | Ja | Ja |
| `champion` setzen | Ja | Ja | Nein | Nein |

MLflow übernimmt:

- Experimente und Runs
- Parameter, Metriken und Systemmetriken
- MLflow-Modellartefakte
- Modellregistrierung und Modellversionen
- Tags und Aliase

Das eigene `mlflow-triton-control`-Plugin übernimmt:

- Erkennung des MLflow-Flavors und des Tasks
- Auswahl des Triton-Templates
- Erzeugung des Triton Model Repository
- Upload in das Repository-S3
- Triton Load und Unload
- Inferenzzugriff für den Smoke-Test

Das Plugin wird erst ab dem Deployment-Schritt benötigt. Es muss im
Argo-Deployment-Container installiert sein. Eine Installation ausschließlich
im MLflow-Server reicht nicht aus, weil der Deployment-Befehl im Argo-Pod
ausgeführt wird.

## MLflow Artifact Store

Die verwaltete MLflow-Installation speichert Metadaten und Modellartefakte
getrennt:

```text
MLflow Tracking Server
        │
        ├── Run- und Registry-Metadaten
        │     └── SQLite: /mlflow-data/mlflow.db
        │
        └── MLflow-Modellartefakte
              └── PVC: /mlflow-data/artifacts
```

Der Python-Client lädt Artefakte über den MLflow-Server hoch und herunter. Das
Trainingsskript sollte MLflow-Modellartefakte nicht selbst mit `boto3`, AWS CLI
oder einem anderen S3-Client verwalten.

Das Triton Model Repository bleibt davon getrennt:

```text
MLflow Artifact Store auf PVC
        │
        │ mlflow-triton-control Plugin
        ▼
Triton Model Repository auf S3
        │
        ▼
Triton Model Load
```

Diese Trennung ist beabsichtigt:

- Der MLflow Artifact Store enthält das reproduzierbare Quellmodell, die
  Signatur und die Python-Abhängigkeiten.
- Das Triton-S3-Repository enthält das ausführbare Deploymentpaket mit
  `model.py`, `config.pbtxt` und der Triton-Modellversion.

## Schritt 1: Training und Registrierung

Da das Training bereits MLflow für Parameter und Metriken verwendet und
zunächst kein separater Validierungsschritt vorgesehen ist, werden Training und
Registrierung in demselben Argo-Pod ausgeführt.

Das vermeidet:

- ein temporäres Modell im Argo Artifact Store
- erneutes Herunterladen in einem Register-Pod
- erneutes Laden des Modells
- ein zusätzliches Register-Container-Image
- die Übergabe einer MLflow Run-ID zwischen zwei Pods

Der Training-Pod erhält mindestens:

```yaml
env:
  - name: MLFLOW_TRACKING_URI
    value: http://mlflow-service.triton-control.svc.cluster.local:5000

  - name: MLFLOW_EXPERIMENT_NAME
    value: model-training

  - name: ARGO_WORKFLOW_NAME
    value: "{{workflow.name}}"

  - name: ARGO_WORKFLOW_UID
    value: "{{workflow.uid}}"
```

### sklearn

Benötigte Tools:

```text
Python
MLflow
scikit-learn
joblib
```

Beispiel:

```python
import os

import mlflow
import mlflow.sklearn
from mlflow.models import infer_signature
from sklearn.metrics import accuracy_score


mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment(
    os.getenv("MLFLOW_EXPERIMENT_NAME", "model-training")
)

with mlflow.start_run(
    run_name=os.getenv("ARGO_WORKFLOW_NAME"),
    log_system_metrics=True,
) as run:
    model.fit(x_train, y_train)

    predictions = model.predict(x_test)
    accuracy = accuracy_score(y_test, predictions)

    mlflow.log_params(
        {
            "framework": "sklearn",
            "task": "classification",
            "model_type": type(model).__name__,
            "random_state": random_state,
        }
    )
    mlflow.log_metric("accuracy", float(accuracy))
    mlflow.set_tags(
        {
            "argo.workflow.name": os.getenv("ARGO_WORKFLOW_NAME", ""),
            "argo.workflow.uid": os.getenv("ARGO_WORKFLOW_UID", ""),
            "deployment.status": "not_deployed",
        }
    )

    signature = infer_signature(x_test, predictions)

    model_info = mlflow.sklearn.log_model(
        sk_model=model,
        name="model",
        signature=signature,
        input_example=x_test[:2],
        registered_model_name="iris-classifier",
    )
```

Der Aufruf von `mlflow.sklearn.log_model()` speichert das MLflow Model im
Artifact Store und erzeugt über `registered_model_name` gleichzeitig eine neue
Registry-Version.

### Hugging Face

Benötigte Tools:

```text
Python
MLflow
torch
transformers
safetensors
tokenizers
```

Beispiel:

```python
import os

import mlflow
import mlflow.transformers
from transformers import pipeline


mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment(
    os.getenv("MLFLOW_EXPERIMENT_NAME", "model-training")
)

with mlflow.start_run(
    run_name=os.getenv("ARGO_WORKFLOW_NAME"),
    log_system_metrics=True,
) as run:
    train_result = trainer.train()
    evaluation_result = trainer.evaluate()

    mlflow.log_metrics(
        {
            key: float(value)
            for key, value in evaluation_result.items()
            if isinstance(value, (int, float))
        }
    )
    mlflow.set_tags(
        {
            "argo.workflow.name": os.getenv("ARGO_WORKFLOW_NAME", ""),
            "argo.workflow.uid": os.getenv("ARGO_WORKFLOW_UID", ""),
            "huggingface.task": "text-classification",
            "deployment.status": "not_deployed",
        }
    )

    inference_pipeline = pipeline(
        task="text-classification",
        model=model,
        tokenizer=tokenizer,
    )

    model_info = mlflow.transformers.log_model(
        transformers_model=inference_pipeline,
        name="model",
        task="text-classification",
        input_example=["Example input"],
        registered_model_name="sentiment-classifier",
        save_pretrained=True,
    )
```

MLflow speichert dabei unter anderem:

```text
MLmodel
config.json
model.safetensors
tokenizer.json
tokenizer_config.json
requirements.txt
conda.yaml
python_env.yaml
```

Der Hugging-Face-Task muss bekannt sein, beispielsweise:

```text
text-classification
feature-extraction
token-classification
text-generation
```

Das Training veröffentlicht als Argo-Output nur die Informationen, die der
Deployment-Schritt benötigt:

```text
model-name=sentiment-classifier
model-version=4
model-uri=models:/sentiment-classifier/4
```

## Plugin-Anforderungen

Die vollständige Beschreibung steht in [MLflow-Triton-Plugin-Anforderungen](mlflow-triton-plugin-requirements.md).

## Schritt 2: Deployment

Der Deployment-Pod benötigt:

```text
MLflow
mlflow-triton-control Plugin
HTTP-Client
Triton-Control-Service-Token
```

Beispiel:

```bash
mlflow deployments create \
  -t triton-control://triton-control-api \
  --endpoint development \
  --name sentiment-classifier \
  -m models:/sentiment-classifier/4
```

`--flavor` ist nicht erforderlich. Das Plugin liest die `MLmodel`-Datei und
wählt den passenden Adapter:

```python
if "transformers" in mlmodel.flavors:
    adapter = HuggingFacePythonAdapter()
elif "sklearn" in mlmodel.flavors:
    adapter = SklearnPythonAdapter()
elif "triton" in mlmodel.flavors:
    adapter = ExistingTritonRepositoryAdapter()
else:
    raise UnsupportedModelFlavor(...)
```

### sklearn Repository

```text
iris_classifier/
├── config.pbtxt
└── 3/
    ├── model.py
    └── model.joblib
```

### Hugging-Face-Repository

```text
sentiment_classifier/
├── config.pbtxt
└── 4/
    ├── model.py
    ├── model/
    │   ├── config.json
    │   └── model.safetensors
    └── tokenizer/
        ├── tokenizer.json
        ├── tokenizer_config.json
        └── special_tokens_map.json
```

### Deployment-Templates

Das Plugin bringt wiederverwendbare Templates mit:

```text
templates/
├── sklearn/
│   ├── classification/
│   └── regression/
└── huggingface/
    ├── text-classification/
    ├── feature-extraction/
    ├── token-classification/
    └── text-generation/
```

Das Hugging-Face-Template basiert auf dem Aufbau der offiziellen NVIDIA
Triton-Python-Backend-Beispiele:

- `TritonPythonModel.initialize()`
- Modell und Tokenizer mit `from_pretrained()` laden
- CPU oder GPU anhand der Triton-Instanz auswählen
- Requests bündeln und Eingaben tokenisieren
- Inferenz mit `torch.inference_mode()` ausführen
- Ergebnisse als Triton-Tensoren zurückgeben

Für neue Hugging-Face-Modelle eines unterstützten Tasks wird kein eigener
Converter benötigt.

## Schritt 3: Smoke-Test

Der Smoke-Test verwendet bevorzugt das Deployment-Plugin:

```bash
mlflow deployments predict \
  -t triton-control://triton-control-api \
  --endpoint development \
  --name sentiment-classifier \
  --input-path smoke-test.json \
  --output-path result.json
```

Das Plugin implementiert dafür:

```python
client.predict(
    deployment_name="sentiment-classifier",
    inputs=payload,
    endpoint="development",
)
```

Nach erfolgreicher Inferenz setzt der Workflow mit normalen MLflow-Funktionen
den Deploymentstatus und den Alias:

```python
from mlflow import MlflowClient


client = MlflowClient()

client.set_model_version_tag(
    name="sentiment-classifier",
    version="4",
    key="deployment_status",
    value="deployed",
)
client.set_model_version_tag(
    name="sentiment-classifier",
    version="4",
    key="triton_endpoint",
    value="development",
)
client.set_registered_model_alias(
    name="sentiment-classifier",
    alias="champion",
    version="4",
)
```

Bei einem fehlgeschlagenen Smoke-Test wird stattdessen
`deployment_status=failed` gesetzt. Der `champion`-Alias bleibt unverändert.

## Argo-DAG

```yaml
templates:
  - name: pipeline
    dag:
      tasks:
        - name: train-and-register
          template: train-and-register

        - name: deploy
          dependencies: [train-and-register]
          template: deploy
          arguments:
            parameters:
              - name: model-name
                value: >-
                  {{tasks.train-and-register.outputs.parameters.model-name}}
              - name: model-version
                value: >-
                  {{tasks.train-and-register.outputs.parameters.model-version}}
              - name: model-uri
                value: >-
                  {{tasks.train-and-register.outputs.parameters.model-uri}}

        - name: smoke-test
          dependencies: [deploy]
          template: smoke-test
          arguments:
            parameters:
              - name: model-name
                value: >-
                  {{tasks.train-and-register.outputs.parameters.model-name}}
              - name: model-version
                value: >-
                  {{tasks.train-and-register.outputs.parameters.model-version}}
```

## Workflow-Parameter für Data Scientists

Ein Data Scientist gibt nur die fachlich notwendigen Parameter an:

```yaml
parameters:
  - name: model-name
    value: sentiment-classifier

  - name: framework
    value: transformers

  - name: task
    value: text-classification

  - name: endpoint
    value: development

  - name: deploy
    value: "true"
```

Für sklearn:

```yaml
framework: sklearn
task: classification
```

Für Hugging Face:

```yaml
framework: transformers
task: text-classification
```

## Container-Images

Für den Ablauf genügen zwei wiederverwendbare Images:

```text
triton-control-training
triton-control-mlflow-deployer
```

### Training und Registrierung

Das Trainingsimage enthält MLflow und das jeweilige Framework.

sklearn:

```text
MLflow 3.x
scikit-learn
joblib
```

Hugging Face:

```text
MLflow 3.x
torch
transformers
safetensors
tokenizers
```

### Deployment und Smoke-Test

```text
MLflow 3.x
mlflow-triton-control Plugin
requests oder httpx
tritonclient[http]
```

## Ergebnis

Für Data Scientists bleibt der Ablauf unabhängig vom Framework:

```text
Workflow starten
→ Training und Metriken erscheinen in MLflow
→ Modell wird in MLflow registriert
→ Modell wird automatisch für Triton verpackt
→ Repository wird in Triton-S3 veröffentlicht
→ Triton lädt das Modell
→ Testinferenz wird ausgeführt
→ Modell erhält den Alias champion
```
