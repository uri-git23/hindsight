# `uvicorn main:app --reload` 로 계속 실행할 수 있도록 남겨둔 진입점. 실제 앱은 app/main.py.
from app.main import app  # noqa: F401
