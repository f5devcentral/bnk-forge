# Runbook — updating a container-runner module (ocibnkctl worked example)

How a new version of an external vendor-CLI runner (published to a git catalog
repo like [`bnkctl-index`](https://github.com/mwiget/bnkctl-index)) is picked up
by BNK Forge so new projects and pipelines run it.

This is the **receiving side**. The producing side — cutting the tool release and
publishing the runner image — lives with the tool (e.g.
[`ocibnkctl/docs/RELEASE.md`](https://github.com/mwiget/ocibnkctl/blob/main/docs/RELEASE.md)).
For the full authoring model (manifests, steps, cluster registration, security),
see [*How to write CI container runner modules and blueprints for BNK
Forge*](./How%20to%20write%20CI%20container%20runner%20modules%20and%20blueprints%20for%20BNK%20Forge.md)
(§9 registration, §9.1 versioning, §10 blueprints).

## The chain

```
tool release (ghcr image, new digest)
        │  bump in the catalog repo (bnkctl-index)
        ▼
  bnkforge.artifact.json + bnkforge.pack.json   (module version + digest)
  forge-blueprint.json                          (blueprint version + module pin)
        │  git push
        ▼
  BNK Forge: sync module source ──► new module_library row (immutable per version)
             sync blueprint source ──► new blueprint_release (discovered)
             import release          ──► deployable
        │
        ▼
  new project from the release → deploy-all → pipeline runs the new image
```

## One-time: register the catalog repo as two sources

`bnkctl-index` carries **both** module manifests (`tools/<name>/`) and blueprints
(`blueprints/<name>/`), so it is registered as **two** sources — a module source
and a blueprint-catalog source — pointed at the same git URL. (Operator/admin
token; `$BNK` is the instance base URL.)

```bash
# Module source (discovers tools/<name>/bnkforge.{pack,artifact}.json)
curl -sk -X POST "$BNK/api/module-sources" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"bnkctl-index","source_type":"git","url":"https://github.com/mwiget/bnkctl-index.git","branch":"main"}'

# Blueprint-catalog source (discovers blueprints/<name>/forge-blueprint.json)
curl -sk -X POST "$BNK/api/blueprint-catalog/sources" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"bnkctl-index","url":"https://github.com/mwiget/bnkctl-index.git","branch":"main"}'
```

Find the IDs later with `GET /api/module-sources` and
`GET /api/blueprint-catalog/sources`.

## Every update: sync + reimport

After the catalog repo is bumped and pushed (new image digest + bumped
`module.version`, artifact `version`, and `blueprint.version` re-pinning the
module — the **bump-on-any-edit** rule of §9.1/§10; an unbumped edit is a
`version_conflict`, not an update):

```bash
MSID=$(curl -sk "$BNK/api/module-sources" -H "Authorization: Bearer $TOKEN" \
        | jq -r '.[] | select(.name=="bnkctl-index") | .id')
BSID=$(curl -sk "$BNK/api/blueprint-catalog/sources" -H "Authorization: Bearer $TOKEN" \
        | jq -r '.[] | select(.name=="bnkctl-index") | .id')

# 1. Sync the module source → the new (source, path, version) becomes a catalog row.
curl -sk -X POST "$BNK/api/module-sources/$MSID/sync" -H "Authorization: Bearer $TOKEN"

# 2. Sync the blueprint source → the bumped blueprint version appears as a new release.
curl -sk -X POST "$BNK/api/blueprint-catalog/sources/$BSID/sync" -H "Authorization: Bearer $TOKEN"

# 3. Import that release to make it deployable.
REL=$(curl -sk "$BNK/api/blueprint-catalog/releases" -H "Authorization: Bearer $TOKEN" \
       | jq -r '[.[] | select(.blueprint_id=="k3s-bnk-demo")] | sort_by(.version) | last | .id')
curl -sk -X POST "$BNK/api/blueprint-catalog/releases/$REL/import" -H "Authorization: Bearer $TOKEN"
```

All of this is also available in the UI (Catalog → Sources → **Sync**; Blueprint
Catalog → the release → **Import**).

## What does and doesn't move

- **New projects** created from the newly-imported release use the new module
  version + image digest immediately.
- **Existing project modules stay pinned** to the version they were created with —
  by design, so a deployed (and destroyable) module keeps the exact manifest it
  ran. Upgrading one is an explicit operator action: **Change Version** in the
  module card menu, or
  `POST /api/project-modules/<id>/change-version {"target_version":"<version>"}`.
  It takes effect on the module's next plan/apply/destroy.
- **Prior versions are never overwritten** — the catalog accumulates one immutable
  row per `(source, path, version)`; `is_latest` marks the newest.

## Gotchas

- **Digest must be live before sync.** The artifact manifest is digest-pinned;
  sync validates the shape but the image must exist at that digest when a deploy
  pulls it. Publish the image first, then bump + sync.
- **`version_conflict` on sync** = you edited a manifest/blueprint without bumping
  its version. Bump and re-sync (§9.1). Never mutate a published version in place.
- **Blueprint pin must exist in the active catalog.** If `forge-blueprint.json`
  pins a module version that wasn't synced/imported, deploy is blocked with
  `BLUEPRINT_MODULE_VERSION_MISSING` — sync the module source before importing the
  blueprint release.
- **The wide docker-socket proxy** (`docker-socket-proxy-infra`) must be enabled
  for ocibnkctl (it builds k3s node containers): `COMPOSE_PROFILES=docker-infra
  make deploy`. See the tool's README prerequisites.
