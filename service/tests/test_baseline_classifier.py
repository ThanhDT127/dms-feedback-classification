from __future__ import annotations

from pathlib import Path

import pytest
from support import write_baseline_artifacts

from dms.exceptions import ModelArtifactError
from dms.pipeline.baseline_classifier import BaselineIssueClassifier


def test_missing_model_artifact_raises(settings, tmp_path: Path):
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir_override = tmp_path / "missing-model"
    with pytest.raises(ModelArtifactError):
        BaselineIssueClassifier(settings)


def test_baseline_loader_without_optional_keyword_minors(settings, tmp_path: Path):
    settings.model_dir_override = tmp_path / "Model"
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    write_baseline_artifacts(settings.model_dir, settings.keyword_dir, include_keyword_minors=False)

    classifier = BaselineIssueClassifier(settings)
    result = classifier.predict_labels_baseline("website đăng nhập bị lỗi")

    assert classifier.keyword_minors == []
    assert "Website" in result
    assert isinstance(result["Website"], bool)


def test_infer_minor_labels_uses_keyword_hints_and_sentiment(settings, tmp_path: Path):
    settings.model_dir_override = tmp_path / "Model"
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    write_baseline_artifacts(settings.model_dir, settings.keyword_dir, include_keyword_minors=True)

    classifier = BaselineIssueClassifier(settings)
    labels, sentiment, hits, brand = classifier.infer_minor_labels("Philips đang khuyến mãi, website bị lỗi")

    assert brand == "Philips"
    assert sentiment == "Tiêu cực"
    assert "Cảm xúc.Tiêu cực" in hits
    assert labels["Hãng"] is True
