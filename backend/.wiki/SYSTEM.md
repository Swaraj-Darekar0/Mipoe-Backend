# SYSTEM.md — Mipoe Backend Wiki

## Governing Schema & Agent Instructions

### 1. Coding Standards
- **Language:** Python 3.9+
- **Frameworks:** FastAPI for HTTP APIs, Celery for background tasks
- **Formatting:** Black, isort, and flake8 compliance
- **Type Hints:** Required for all public functions and classes
- **Docstrings:** All modules, classes, and public methods must have docstrings

### 2. State Management Rules
- **FastAPI:**
  - Stateless request handling
  - Dependency injection for DB/session/context
  - No global mutable state
- **Celery:**
  - Tasks must be idempotent
  - Use Redis or DB for cross-process state
  - Results and errors must be logged

### 3. Agent Instructions
- **Consult this wiki before editing code.**
- **Update the relevant .wiki file(s) before making significant code changes.**
- **If code and wiki disagree, the wiki is the source of truth.**
- **Document new patterns, endpoints, or architectural changes here.**

### 4. Review & Maintenance
- All PRs must reference relevant .wiki updates
- Outdated wiki content is a critical bug
