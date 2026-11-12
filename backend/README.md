# Prism Backend

The Prism backend is an asynchronous FastAPI application organized as a modular monolith.

## Local development

Create and activate a virtual environment, then install the backend with development tools:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Copy the repository-level `.env.example` to `.env`, then start the API from this directory:

```powershell
uvicorn prism.main:app --reload
```

Run the checks:

```powershell
ruff format --check .
ruff check .
mypy src
pytest
```

