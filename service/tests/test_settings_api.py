from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dms.settings import Settings, get_settings
from dms.web.app import create_app
from dms.web import deps


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
        encoding="utf-8"
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
    monkeypatch.setattr("dms.web.api.pipeline_api.deps.get_settings", lambda: Settings(
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
    ))
    monkeypatch.setattr("dms.web.deps.get_settings", lambda: Settings(
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
    ))
    
    # Clear cache and reset deps
    get_settings.cache_clear()
    deps.reset()
    
    app = create_app()
    return TestClient(app)


def test_get_settings(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["azure_tenant_id"] == "tenant"
    assert data["gemini_backend"] == "vertex"


def test_put_settings_success(client, tmp_path):
    # Change backend to apikey and provide gemini_api_key
    payload = {
        "backend": "api_key",
        "gemini_api_key": "new-secret-key-12345"
    }
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    
    # Verify .env has been modified
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GEMINI_BACKEND=apikey" in env_content
    assert "GEMINI_API_KEY=new-secret-key-12345" in env_content


def test_put_settings_validation_and_rollback(client, tmp_path):
    # Invalid setting: backend is apikey but GEMINI_API_KEY is empty
    payload = {
        "backend": "api_key",
        "gemini_api_key": ""
    }
    
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert "Cấu hình không hợp lệ" in response.json()["detail"]
    
    # Verify rollback: .env remains intact with previous content
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GEMINI_BACKEND=vertex" in env_content
    assert "GEMINI_API_KEY" not in env_content


def test_put_prompt_validation(client, tmp_path):
    # Missing required placeholders
    payload = {
        "prompt": "Hello world!"
    }
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
    payload = {
        "Báo lỗi": ["hỏng", "cháy", "vỡ"],
        "HTPP": ["phân phối", "tràn vùng"]
    }
    response = client.put("/api/pipeline/keywords", json=payload)
    assert response.status_code == 200
    
    # Verify JSON was written
    kw_file = tmp_path / "Keyword" / "kw_map.json"
    assert kw_file.is_file()
    data = json.loads(kw_file.read_text(encoding="utf-8"))
    assert data["Báo lỗi"] == ["hỏng", "cháy", "vỡ"]


def test_products_list_and_save(client, tmp_path):
    # Create mock product catalog excel file first
    import pandas as pd
    products_file = tmp_path / "Keyword" / "Phân Chia Nhóm Sản Phẩm V2.xlsx"
    df = pd.DataFrame([
        {"Sản phẩm": "Đèn Led", "Dòng SP": "Bulb", "Model": "LED-BULB-9W"},
        {"Sản phẩm": "Thiết bị điện", "Dòng SP": "Ổ cắm", "Model": "OC-4D-3M"}
    ])
    df.to_excel(products_file, index=False)
    
    # 1. Test GET list
    response = client.get("/api/pipeline/products/list")
    assert response.status_code == 200
    data = response.json()
    assert "columns" in data
    assert len(data["products"]) == 2
    assert data["products"][0]["Model"] == "LED-BULB-9W"
    
    # 2. Test PUT list
    new_products = [
        {"Sản phẩm": "Đèn Led", "Dòng SP": "Bulb", "Model": "LED-BULB-9W-V2"},
        {"Sản phẩm": "Thiết bị điện", "Dòng SP": "Ổ cắm", "Model": "OC-4D-3M"}
    ]
    response = client.put("/api/pipeline/products", json=new_products)
    assert response.status_code == 200
    
    # Verify excel file has been updated
    updated_df = pd.read_excel(products_file)
    assert len(updated_df) == 2
    assert updated_df.iloc[0]["Model"] == "LED-BULB-9W-V2"
