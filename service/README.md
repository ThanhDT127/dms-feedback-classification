# DMS Feedback Classification Service

This service is packaged from `service/` and runs with `python -m dms`.

## Runtime assets

The service needs more than source code:

- `.env`
- `testvertex.json` or another service-account credential file
- `Keyword/`
- `Model/`

Issue classification now follows the notebook baseline-plus-refine flow, so `Model/` is a required runtime dependency.

Required model artifacts:

- `tfidf_word.pkl`
- `tfidf_char.pkl`
- `ovr_logreg.pkl`
- `best_thresholds.json`
- `label_cols.json`

Optional model artifact:

- `keyword_minors.json`

Extra files may exist in `Model/` and are ignored unless code explicitly loads them.

Compatibility note:

- the current production artifacts were serialized with `scikit-learn 1.6.1`
- runtime dependencies should stay on a compatible `1.6.x` line unless the artifacts are regenerated

## Docker compose

From `service/`, the compose file expects:

- `./Keyword` mounted read-only
- `./Model` mounted read-only
- `./testvertex.json` mounted read-only
- `./work` mounted read-write
- `./logs` mounted read-write

If `Model/` is missing or incomplete, startup fails fast with a model-artifact error.

## SharePoint-backed asset sync

The watcher can also pull tracked `Keyword/` and `Model/` files from SharePoint automatically.

- it checks SharePoint metadata before each polling cycle
- it only downloads tracked assets when the remote version changed
- it validates model artifacts as a bundle before activating them
- it reloads matcher/classifier dependencies only between cycles
- if a later sync fails, it keeps using the last known good local snapshot

Relevant settings:

- `ENABLE_SHAREPOINT_CONFIG_SYNC=true`
- `SHAREPOINT_KEYWORD_FOLDER=Keyword`
- `SHAREPOINT_MODEL_FOLDER=Model`

Sync state is stored in `work/config_assets_state.json`.

## Runtime cleanup

The watcher can clean up temporary local artifacts automatically.

- deletes per-file `work/input`, `work/output`, and `work/checkpoint` artifacts after a file is processed, uploaded, and marked `done`
- removes stale `work/config_assets/cfgsync-*` staging folders
- applies TTL-based cleanup to old files in `work/output/` and `logs/`

Protected state is preserved:

- `work/seen_files.json`
- `work/metrics.json`
- `work/health.json`
- `work/config_assets_state.json`
- `work/config_assets/active/`
