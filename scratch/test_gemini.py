import sys
import os
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(script_dir / "service" / "src"))

from dms.settings import get_settings
from dms.gemini_client import GeminiClient

print("Loading settings...")
settings = get_settings()
print("Settings loaded. Backend:", settings.gemini_backend, "Model:", settings.gemini_model)

print("Initializing GeminiClient...")
client = GeminiClient(settings)

print("Testing simple generation...")
try:
    response = client.generate("Xin chào, bạn có nghe rõ không? Hãy trả lời ngắn gọn 'Có'.")
    print("Response:", response)
except Exception as e:
    print("Error during generation:", e)
