{{/*
Expand the name of the chart.
*/}}
{{- define "corshub.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncated to 63 characters to comply with DNS label limits.
*/}}
{{- define "corshub.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create the chart label value (name-version).
*/}}
{{- define "corshub.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels attached to every resource.
*/}}
{{- define "corshub.labels" -}}
helm.sh/chart: {{ include "corshub.chart" . }}
{{ include "corshub.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — stable subset used by Services and HPAs to select pods.
*/}}
{{- define "corshub.selectorLabels" -}}
app.kubernetes.io/name: {{ include "corshub.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve the ServiceAccount name.
*/}}
{{- define "corshub.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "corshub.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Resolve the container image reference (repository:tag).
The tag defaults to the chart appVersion when left empty.
*/}}
{{- define "corshub.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}

{{/*
OPA sidecar image reference (repository:tag).
*/}}
{{- define "corshub.opa.image" -}}
{{- printf "%s:%s" .Values.opa.image.repository .Values.opa.image.tag }}
{{- end }}

{{/*
Name of the ConfigMap that holds the OPA Rego policy files.
*/}}
{{- define "corshub.opa.policiesConfigMapName" -}}
{{- printf "%s-opa-policies" (include "corshub.fullname" .) }}
{{- end }}

{{/*
Name of the ConfigMap that holds the OPA registry data (base stations and rovers).
*/}}
{{- define "corshub.opa.dataConfigMapName" -}}
{{- printf "%s-opa-data" (include "corshub.fullname" .) }}
{{- end }}
