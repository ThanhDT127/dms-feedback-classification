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
