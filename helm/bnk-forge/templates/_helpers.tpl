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
{{/* bonnyr-f5 #193 M10: render-time CORS/production guard, mirroring the backend's
     core/config.py validate_production() SystemExit conditions exactly, so a fatal
     posture is caught at `helm install` time instead of as a crashloop:
       * a bare "*" ENTRY in ALLOWED_ORIGINS -> fatal under staging AND production
         (exact comma-split entry, matching the backend's `"*" in self.cors_origins`
         since r4 — so a legit `https://*.example.com` subdomain origin is NOT flagged);
       * "localhost" substring in ALLOWED_ORIGINS -> fatal under production only
         (matches the backend's per-origin `"localhost" in origin`).
     An EMPTY ALLOWED_ORIGINS is deliberately NOT failed: the backend accepts it
     (no wildcard, no localhost) and boots, so the default render (ENVIRONMENT
     production + the empty ALLOWED_ORIGINS shipped in values.yaml) stays green under
     `helm lint` and a bare `helm template` — this guard fires only once an operator
     puts a genuinely fatal value in a real production/staging posture. Mirrors the
     deterministic fail-at-render pattern secrets.yaml uses for mcpPassword/mcpUsername. */}}
{{- $benv := .Values.api.env | default dict -}}
{{- $environment := $benv.ENVIRONMENT | default "" -}}
{{- if or (eq $environment "production") (eq $environment "staging") -}}
{{- $origins := $benv.ALLOWED_ORIGINS | default "" -}}
{{- $hasWildcard := false -}}
{{- range (splitList "," $origins) -}}{{- if eq (trim .) "*" -}}{{- $hasWildcard = true -}}{{- end -}}{{- end -}}
{{- if $hasWildcard -}}
{{- fail (printf "api.env.ALLOWED_ORIGINS has a bare '*' (wildcard) entry under ENVIRONMENT=%s; the backend rejects this at boot (validate_production) and crashloops. Set api.env.ALLOWED_ORIGINS to your explicit origin(s), e.g. https://forge.example.com." $environment) -}}
{{- end -}}
{{- if and (eq $environment "production") (contains "localhost" $origins) -}}
{{- fail "api.env.ALLOWED_ORIGINS contains 'localhost' under ENVIRONMENT=production; the backend rejects this at boot (validate_production) and crashloops. Set api.env.ALLOWED_ORIGINS to your actual domain/IP, e.g. https://forge.example.com (or set ENVIRONMENT=development for a local trial)." -}}
{{- end -}}
{{- end -}}
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
# shipped `changeme` default just leaves the mcp account unseeded and the client
# can never authenticate ("removes the default without plumbing the replacement";
# nothing is generated for MCP under the #188-over-#186 consolidation, bonnyr-f5
# #193). Source both from the release Secret's mcp-* keys, identical to mcp.yaml.
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

{{/*
secretsChecksum: digest for the pod `checksum/secret` annotations, so a real secret change
rolls api/worker/beat/mcp and an unchanged render does not. bonnyr-f5 #193 M7 + round-3 minor.
It hashes the DETERMINISTIC inputs that determine the Secret -- the values.yaml `secrets.*`
block and the persisted Secret's `.data` (reused verbatim across upgrades via `lookup`,
nil-folded as secrets.yaml does) -- NOT the rendered Secret.

The M7 trilemma (r4 self-review): you cannot have all three of (i) stable across a bare
no-cluster `helm template`, (ii) tracks a GENERATED-value rotation at render time, and
(iii) unpredictable secrets. Determinism buys (i)+(ii) but forfeits (iii) -- a derived key is
computable from public chart/label metadata (release name/namespace/fullname), handing an
attacker the JWT signing key, the at-rest Fernet key and the admin/mcp passwords. We keep
(iii) (randAlphaNum) and get (i) from hashing inputs. Coverage:
  - operator edit (`values.secrets.*`)        -> input changes  -> digest moves, pods roll NOW;
  - a change to the persisted Secret          -> `.data` changes (seen via lookup in a REAL
                                                 cluster)        -> digest moves;
  - rotate-away-from-a-persisted-known-default emits a fresh RANDOM value the checksum cannot
    see at render time without re-rolling the random (or a cluster), so those pods roll on the
    NEXT reconcile once the value persists into `.data` -- one sync, not never.
(An earlier "rotating-from-default" marker was removed: it was provably redundant -- the same
`.data` change already moves the digest, so the marker never altered the outcome. A bare
no-cluster `helm template` has no lookup and reflects only `values.secrets`; that is a
template-mode limitation, not a deploy defect -- GitOps renders against the live cluster.)
*/}}
{{- define "bnk-forge.secretsChecksum" -}}
{{- $name := printf "%s-secrets" (include "bnk-forge.fullname" .) -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace $name -}}
{{- $data := dict -}}
{{- if and $existing $existing.data -}}{{- $data = $existing.data -}}{{- end -}}
{{- printf "%s|%s" (toYaml .Values.secrets) (toYaml $data) | sha256sum -}}
{{- end -}}
