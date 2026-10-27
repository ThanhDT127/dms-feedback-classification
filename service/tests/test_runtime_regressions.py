import ast
from pathlib import Path

import dms.auth as auth_module
from dms.auth import AuthProvider
from dms.classification_jobs import ClassificationJobStore


def test_msal_requests_have_finite_timeout(settings, monkeypatch):
    captured = {}
    fake_app = object()

    def create_app(**kwargs):
        captured.update(kwargs)
        return fake_app

    monkeypatch.setattr(auth_module.msal, "ConfidentialClientApplication", create_app)
    assert AuthProvider(settings).msal_app is fake_app
    assert captured.get("timeout") == 30.0


def test_internal_job_list_calls_match_store_api(tmp_path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    root = Path(auth_module.__file__).parent
    checked = []
    for relative in ("watcher.py", "web/api/files.py", "web/api/metrics_api.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "list_jobs":
                kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
                assert store.list_jobs(**kwargs) == []
                checked.append((relative, node.lineno))
    assert len(checked) == 5
