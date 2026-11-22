{{- define "triton-control.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "triton-control.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "triton-control.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "triton-control.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "triton-control.selectorLabels" -}}
app.kubernetes.io/name: {{ include "triton-control.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "triton-control.postgresqlName" -}}
{{- printf "%s-postgresql" (include "triton-control.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "triton-control.postgresqlSecretName" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- else -}}
{{- printf "%s-postgresql" (include "triton-control.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "triton-control.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "triton-control.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Installation mode is a literal startup value, never a request/database override. */}}
{{- define "triton-control.installationMode" -}}
{{- $mode := "development" -}}
{{- $seen := false -}}
{{- range .Values.app.env -}}
{{- if eq .name "TRITON_CONTROL_MODE" -}}
{{- if $seen -}}{{ fail "Configure TRITON_CONTROL_MODE only once" }}{{- end -}}
{{- if .valueFrom -}}{{ fail "TRITON_CONTROL_MODE must be a literal Helm value" }}{{- end -}}
{{- $mode = .value -}}
{{- $seen = true -}}
{{- end -}}
{{- end -}}
{{- if not (has $mode (list "development" "production")) -}}
{{- fail "TRITON_CONTROL_MODE must be development or production" -}}
{{- end -}}
{{- $mode -}}
{{- end -}}

{{- define "triton-control.releaseSourcesConfigMapName" -}}
{{- $identity := list .Release.Name .Values.releaseSources.file | mustToJson | sha256sum | trunc 12 -}}
{{- printf "%s-sources-%s" (include "triton-control.fullname" . | trunc 42 | trimSuffix "-") $identity -}}
{{- end -}}

{{- define "triton-control.builderImage" -}}
{{- $kaniko := .Values.buildQualification.kaniko -}}
{{- dict "release" $kaniko.version "executor_image" $kaniko.image | toJson -}}
{{- end -}}

{{- define "triton-control.buildPolicyJson" -}}
{{- $defaults := include "triton-control.builderImage" . | fromJson -}}
{{- mergeOverwrite $defaults .Values.buildQualification.policy | toJson -}}
{{- end -}}

{{/* A registry policy takes precedence. Production installations cannot build local images. */}}
{{- define "triton-control.imageBuilds" -}}
{{- mergeOverwrite (deepCopy .Values.imageBuilds) (default dict .Values.localBuilds) | toJson -}}
{{- end -}}

{{- define "triton-control.localBuildsEnabled" -}}
{{- if and (eq (include "triton-control.installationMode" .) "development") (not .Values.buildQualification.policy) -}}
true
{{- end -}}
{{- end -}}
