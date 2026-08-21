{{/* Common helpers for bnk-forge chart */}}

{{- define "bnk-forge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bnk-forge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "bnk-forge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bnk-forge.labels" -}}
helm.sh/chart: {{ include "bnk-forge.chart" . }}
app.kubernetes.io/name: {{ include "bnk-forge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "bnk-forge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bnk-forge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
componentLabels: include component=<name> with selector labels.
Usage: {{- include "bnk-forge.componentLabels" (list . "api") | nindent 4 }}
*/}}
{{- define "bnk-forge.componentLabels" -}}
{{- $ctx := index . 0 -}}
{{- $component := index . 1 -}}
{{ include "bnk-forge.selectorLabels" $ctx }}
app.kubernetes.io/component: {{ $component }}
{{- end -}}

{{- define "bnk-forge.image" -}}
{{- $ctx := index . 0 -}}
{{- $img := index . 1 -}}
{{- $tag := default $ctx.Values.image.tag $img.tag -}}
{{- printf "%s/%s:%s" $ctx.Values.global.imageRegistry $img.repository $tag -}}
{{- end -}}

{{- define "bnk-forge.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
sharedStorageClass: returns the shared (RWX) storageClassName, or empty string.
*/}}
{{- define "bnk-forge.sharedStorageClass" -}}
{{- default "" .Values.global.sharedStorageClass -}}
{{- end -}}

{{- define "bnk-forge.databaseStorageClass" -}}
{{- $sc := .Values.postgres.persistence.storageClass | default .Values.global.databaseStorageClass -}}
{{- $sc | default "" -}}
{{- end -}}

{{/*
backendEnv: env block shared by api/worker/beat. Wires DB + Redis URLs to
in-cluster services and pulls secrets from the generated Secret.
*/}}
{{- define "bnk-forge.backendEnv" -}}
- name: POSTGRES_HOST
  value: {{ include "bnk-forge.fullname" . }}-postgres
- name: REDIS_HOST
  value: {{ include "bnk-forge.fullname" . }}-redis
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: postgres-password
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: redis-password
- name: JWT_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: jwt-secret-key
- name: ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: encryption-key
# #184: seed the admin account from a generated secret, never a shipped
# default. Retrieve with:
#   kubectl get secret <release>-bnk-forge-secrets -o jsonpath='{.data.admin-password}' | base64 -d
- name: DEFAULT_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: admin-password
# #186: plumb the must-change gate alongside its sibling, or the seeded admin
# owes a password change no route accepts (login is exempt; every other /api
# route 403s). Not a secret -- a plain value. Quote so the bool renders "true"/
# "false" (do NOT `default` it: a bool false collapses back to the default).
- name: DEFAULT_ADMIN_MUST_CHANGE
  # #186 (bonnyr-f5 r4): fall back to the secure "true" only when the value is
  # nil/unset -- `| default true` cannot be used here because sprig `default`
  # treats a bool false as empty and would silently flip an intentional false
  # back to true. kindIs "invalid" is true only for nil, so an explicit false
  # still renders "false"; nil no longer renders a bare `value:` that makes
  # pydantic reject an empty string and the backend crashloop.
  value: {{ if kindIs "invalid" .Values.secrets.adminMustChange }}{{ "true" | quote }}{{ else }}{{ .Values.secrets.adminMustChange | quote }}{{ end }}
# #186 BLOCKER 1 / #187 (bonnyr-f5 r5): the backend reconciles the mcp service
# account to MCP_SERVICE_PASSWORD on every boot, so it must read the SAME
# per-install secret the mcp client (mcp.yaml) reads -- otherwise removing the
# shipped `changeme` default just makes the backend generate its own secret the
# client can never match ("removes the default without plumbing the replacement").
# Source both from the release Secret's mcp-* keys, identical to mcp.yaml.
- name: MCP_SERVICE_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: mcp-username
- name: MCP_SERVICE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bnk-forge.fullname" . }}-secrets
      key: mcp-password
- name: DATABASE_URL
  value: "postgresql://bnkforge:$(POSTGRES_PASSWORD)@$(POSTGRES_HOST):5432/bnkforge"
- name: REDIS_URL
  value: "redis://:$(REDIS_PASSWORD)@$(REDIS_HOST):6379/0"
- name: CELERY_BROKER_URL
  value: "redis://:$(REDIS_PASSWORD)@$(REDIS_HOST):6379/0"
- name: CELERY_RESULT_BACKEND
  value: "redis://:$(REDIS_PASSWORD)@$(REDIS_HOST):6379/0"
- name: TF_PLUGIN_CACHE_DIR
  value: /app/provider-cache
{{- range $k, $v := .Values.api.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{/*
sharedVolumeMounts: mount blocks for every shared volume. Used by api/worker/beat.
*/}}
{{- define "bnk-forge.sharedVolumeMounts" -}}
{{- range $k, $v := .Values.sharedVolumes }}
- name: {{ kebabcase $k }}
  mountPath: {{ $v.mountPath }}
{{- end }}
{{- if .Values.externalSecretsRef.name }}
- name: external-secrets
  mountPath: /app/secrets
  readOnly: true
{{- end }}
{{- end -}}

{{/*
sharedVolumes: PVC-backed volume references for pod spec.
*/}}
{{- define "bnk-forge.sharedVolumes" -}}
{{- $fullname := include "bnk-forge.fullname" . -}}
{{- range $k, $v := .Values.sharedVolumes }}
- name: {{ kebabcase $k }}
  persistentVolumeClaim:
    claimName: {{ $fullname }}-{{ kebabcase $k }}
{{- end }}
{{- if .Values.externalSecretsRef.name }}
- name: external-secrets
  secret:
    secretName: {{ .Values.externalSecretsRef.name }}
{{- end }}
{{- end -}}
