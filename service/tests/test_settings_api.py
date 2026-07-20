from __future__ import annotations

import json

import pytest
from conftest import apply_auth_overrides
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from dms.settings import Settings, get_settings, update_env_file
from dms.web import deps
from dms.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Ensure complete isolation from host environment variables
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_BACKEND", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHAREPOINT_DRIVE_ID", raising=False)
    monkeypatch.delenv("SHAREPOINT_ROOT_FOLDER_ID", raising=False)

    # Set up mock .env file with valid settings so it passes baseline validation
    mock_env = tmp_path / ".env"
    mock_env.write_text(
        "AZURE_TENANT_ID=tenant\n"
        "AZURE_CLIENT_ID=client\n"
        "AZURE_CLIENT_SECRET=secret\n"
        "SHAREPOINT_DRIVE_ID=drive\n"
        "SHAREPOINT_ROOT_FOLDER_ID=root\n"
        "GEMINI_BACKEND=vertex\n"
        "GCP_PROJECT_ID=project\n",
        encoding="utf-8",
    )

    # Mock Settings model_config to use mock_env
    new_config = dict(Settings.model_config)
    new_config["env_file"] = str(mock_env)
    monkeypatch.setattr(Settings, "model_config", new_config)

    # Mock settings directories
    kw_dir = tmp_path / "Keyword"
    kw_dir.mkdir()

    # Mock Settings properties and SERVICE_DIR
    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    monkeypatch.setattr("dms.web.api.settings_api.SERVICE_DIR", tmp_path)
    monkeypatch.setattr(
        "dms.web.api.pipeline_api.deps.get_settings",
        lambda: Settings(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            keyword_dir_override=kw_dir,
            data_dir=tmp_path,
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
        ),
    )
    monkeypatch.setattr(
        "dms.web.deps.get_settings",
        lambda: Settings(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            keyword_dir_override=kw_dir,
            data_dir=tmp_path,
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
        ),
    )

    # Clear cache and reset deps
    get_settings.cache_clear()
    deps.reset()

    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)


def test_get_settings(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["azure_tenant_id"] == "tenant"
    assert data["gemini_backend"] == "vertex"


def test_put_settings_success(client, tmp_path):
    # Change backend to apikey and provide gemini_api_key
    payload = {"backend": "api_key", "gemini_api_key": "new-secret-key-12345"}
    from unittest.mock import patch

    with patch("dms.gemini_client.GeminiClient.generate", return_value="xin chào"):
        response = client.put("/api/settings", json=payload)
    assert response.status_code == 200

    # Verify .env has been modified
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GEMINI_BACKEND=apikey" in env_content
    assert "GEMINI_API_KEY=new-secret-key-12345" in env_content

    # PUT /api/settings calls deps.reset() internally which clears auth overrides
    apply_auth_overrides(client.app)
    # Secret readback endpoint is removed for security.
    secret = client.get("/api/settings/secret/gemini_api_key")
    assert secret.status_code == 404


def test_update_env_file_quotes_special_values(tmp_path, monkeypatch):
    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=old\nPLAIN=value\n",
        encoding="utf-8",
    )

    update_env_file(
        {
            "GEMINI_API_KEY": 'abc#def=ghi"jkl',
            "MULTILINE_SECRET": "line1\nline2",
            "SPACEY": " value ",
        }
    )

    parsed = dotenv_values(tmp_path / ".env")
    assert parsed["GEMINI_API_KEY"] == 'abc#def=ghi"jkl'
    assert parsed["MULTILINE_SECRET"] == "line1\nline2"
    assert parsed["SPACEY"] == " value "


def test_put_settings_validation_and_rollback(client, tmp_path):
    # Invalid setting: backend is apikey but GEMINI_API_KEY is empty
    payload = {"backend": "api_key", "gemini_api_key": ""}

    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert "Cấu hình không hợp lệ" in response.json()["detail"]

    # Verify rollback: .env remains intact with previous content
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GEMINI_BACKEND=vertex" in env_content
    assert "GEMINI_API_KEY" not in env_content


def test_put_prompt_validation(client, tmp_path):
    # Missing required placeholders
    payload = {"prompt": "Hello world!"}
    response = client.put("/api/settings/prompt", json=payload)
    assert response.status_code == 400
    assert "Thiếu các placeholder bắt buộc" in response.json()["detail"]

    # Valid placeholders
    payload = {
        "prompt": "Test prompt: {minor_order_json} {label_defs} {hints_json} {brand_json} {input_json}"
    }
    response = client.put("/api/settings/prompt", json=payload)
    assert response.status_code == 200

    # Verify prompt saved
    prompt_file = tmp_path / "Keyword" / "system_prompt.txt"
    assert prompt_file.is_file()
    assert "Test prompt" in prompt_file.read_text(encoding="utf-8")


def test_put_pipeline_keywords(client, tmp_path):
    payload = {"Báo lỗi": ["hỏng", "cháy", "vỡ"], "HTPP": ["phân phối", "tràn vùng"]}
    response = client.put("/api/pipeline/keywords", json=payload)
    assert response.status_code == 200

    # Verify JSON was written
    kw_file = tmp_path / "Keyword" / "kw_map.json"
    assert kw_file.is_file()
    data = json.loads(kw_file.read_text(encoding="utf-8"))
    assert data["Báo lỗi"] == ["hỏng", "cháy", "vỡ"]


def test_keyword_search_exact_and_partial(client):
    payload = {"Báo lỗi": ["hỏng", "cháy", "vỡ"], "HTPP": ["phân phối", "tràn vùng"]}
    response = client.put("/api/pipeline/keywords", json=payload)
    assert response.status_code == 200

    exact = client.get("/api/pipeline/keywords/search", params={"q": "hỏng"})
    assert exact.status_code == 200
    assert exact.json()["results"][0] == {"keyword": "hỏng", "group": "Báo lỗi"}

    partial = client.get("/api/pipeline/keywords/search", params={"q": "phân"})
    assert partial.status_code == 200
    assert {"keyword": "phân phối", "group": "HTPP"} in partial.json()["results"]

    empty = client.get("/api/pipeline/keywords/search", params={"q": ""})
    assert empty.status_code == 200
    assert empty.json()["results"] == []


def test_products_list_and_save(client, tmp_path):
    # Create mock product catalog excel file first with 3 sheets
    import pandas as pd

    products_file = tmp_path / "Keyword" / "Phân Chia Nhóm Sản Phẩm V2.xlsx"

    df1 = pd.DataFrame(
        [
            {"Sản phẩm": "Đèn Led", "Dòng SP": "Bulb", "Model": "LED-BULB-9W"},
            {"Sản phẩm": "Thiết bị điện", "Dòng SP": "Ổ cắm", "Model": "OC-4D-3M"},
        ]
    )
    df2 = pd.DataFrame([{"Sản phẩm": "Ấm siêu tốc", "Dòng SP": "RD-AST18", "Từ khóa": "Ast18"}])
    df3 = pd.DataFrame([{"Sản phẩm": "Aptomat", "Từ khóa": "át khối"}])

    with pd.ExcelWriter(products_file, engine="openpyxl") as writer:
        df1.to_excel(writer, sheet_name="Lọc lần 1", index=False)
        df2.to_excel(writer, sheet_name="Lọc lần 2", index=False)
        df3.to_excel(writer, sheet_name="Lọc lần 3", index=False)

    # 1. Test GET list (all sheets)
    response = client.get("/api/pipeline/products/list")
    assert response.status_code == 200
    data = response.json()
    assert "sheets" in data
    assert "Lọc lần 1" in data["sheet_names"]
    assert "Lọc lần 2" in data["sheet_names"]
    assert "Lọc lần 3" in data["sheet_names"]
    assert len(data["sheets"]["Lọc lần 1"]["products"]) == 2
    assert data["sheets"]["Lọc lần 1"]["products"][0]["Model"] == "LED-BULB-9W"
    assert len(data["sheets"]["Lọc lần 2"]["products"]) == 1
    assert data["sheets"]["Lọc lần 2"]["products"][0]["Từ khóa"] == "Ast18"

    # 2. Test PUT list (updating Sheet 2 while preserving Sheet 1 and 3)
    payload = {
        "sheet_name": "Lọc lần 2",
        "products": [
            {"Sản phẩm": "Ấm siêu tốc", "Dòng SP": "RD-AST18", "Từ khóa": "Ast18-Updated"}
        ],
    }
    response = client.put("/api/pipeline/products", json=payload)
    assert response.status_code == 200

    # Verify all sheets are preserved, and only the target sheet is updated!
    with pd.ExcelFile(products_file) as xl:
        assert list(xl.sheet_names) == ["Lọc lần 1", "Lọc lần 2", "Lọc lần 3"]
        updated_df1 = pd.read_excel(xl, "Lọc lần 1")
        updated_df2 = pd.read_excel(xl, "Lọc lần 2")
        updated_df3 = pd.read_excel(xl, "Lọc lần 3")

        assert len(updated_df1) == 2
        assert updated_df1.iloc[0]["Model"] == "LED-BULB-9W"
        assert len(updated_df2) == 1
        assert updated_df2.iloc[0]["Từ khóa"] == "Ast18-Updated"
        assert len(updated_df3) == 1
        assert updated_df3.iloc[0]["Sản phẩm"] == "Aptomat"
