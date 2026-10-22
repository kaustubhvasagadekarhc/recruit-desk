"""Vercel entrypoint (pyproject.toml: [tool.vercel] entrypoint). Settings come from the project's environment variables."""
from app.main import create_app

app = create_app()
