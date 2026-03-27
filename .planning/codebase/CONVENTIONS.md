# Coding Conventions

**Analysis Date:** 2026-03-27

## Naming Patterns

**Files (Python):**
- Module files: `snake_case.py` (e.g., `database.py`, `job_manager.py`, `ref_align.py`)
- Route files: named by functionality (e.g., `routes/generation.py`, `routes/influencers.py`, `routes/parser.py`)
- Test files: `qa_test.py` or `test_*.py` pattern (no formal test framework in current codebase, E2E tests via Playwright)

**Files (TypeScript/React):**
- Page components: `PascalCase` + `Page` suffix (e.g., `AvatarDetailPage.tsx`, `TaskDetailPage.tsx`)
- Regular components: `PascalCase.tsx` (e.g., `ImageWithFallback.tsx`, shadcn/ui components in `components/ui/`)
- API client/services: `lowercase.ts` (e.g., `client.ts`, `hooks.ts`, `mappers.ts`, `sse.ts`, `types.ts`)
- CSS files: `lowercase.css` (e.g., `index.css`)

**Functions:**
- Python: `snake_case` for regular functions, prefix with underscore for internal helpers (e.g., `_save_generation_job`, `_report`, `_resolve_run_id`)
- TypeScript: `camelCase` for regular functions (e.g., `formatViews`, `getStatusIcon`, `useQuery`), PascalCase only for React components
- Async functions: prefixed with `async` keyword, no special naming (e.g., `async def connect()`, `async function request()`)

**Variables:**
- Python: `snake_case` (e.g., `progress_callback`, `stage_totals`, `db_path`)
- TypeScript: `camelCase` (e.g., `baseUrl`, `timeoutMs`, `authToken`)
- Constants: `UPPERCASE` with underscores in Python (e.g., `DEFAULT_TIMEOUT_MS`, `PROJECT_ROOT`, `API_PREFIX`)
- Object keys in config/data structures: `snake_case` for database/API consistency (e.g., `file_name`, `run_id`, `job_id`)

**Types:**
- Python: Class names `PascalCase` (e.g., `Database`, `PersistentJobManager`, `ServerManager`, `EventBus`)
- TypeScript: Interface names `PascalCase` + optional suffix (e.g., `InfluencerOut`, `JobInfo`, `UseQueryResult`, `LoginRequest`)
- Pydantic models: `PascalCase` with suffixes indicating direction (e.g., `PipelineRunRequest` for input, `PipelineRunOut` for output)

## Code Style

**Formatting:**
- Python: 2-space indentation observed in some files, 4-space standard in others; no strict formatter configured
- TypeScript: 2-space indentation, no enforced formatter (no `.prettierrc` or `eslintrc.json` in frontend/)

**Line length:**
- Python: ~100-120 characters (observed in code)
- TypeScript: ~100-120 characters (observed in code)

**Imports:**
- Python: Use `from __future__ import annotations` at top of file for forward-compatible type hints (seen in all API and core modules)
- Python: Group imports: stdlib → third-party → local, one per line
- TypeScript: Group imports by: type imports (`import type { ... }`) → regular imports → side-effect imports

**Import organization example (Python):**
```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel

from api.database import Database
from trend_parser.config import ParserConfig
```

**Import organization example (TypeScript):**
```typescript
import type { InfluencerOut, JobInfo } from "./types";
import { useState, useEffect } from "react";
import { api } from "../api/client";
import "./styles/index.css";
```

**Path aliases:**
- TypeScript: `@` alias points to `./src` directory (configured in `vite.config.ts`)
- Usage: `import { api } from "@/app/api/client"`

## Error Handling

**Patterns:**
- Python: Explicit exception catching with specific types (e.g., `except (json.JSONDecodeError, TypeError)`, `except sqlite3.OperationalError`)
- Python: Wrap exceptions with context-specific messages; log before re-raising when appropriate
- Python: Use `logger.info()`, `logger.debug()`, `logger.warning()` consistently — never bare `print()`
- TypeScript: Custom `ApiError` class extends `Error` with status code and message (see `api/client.ts`)
- TypeScript: `.catch()` chains check for abort errors specifically: `err instanceof DOMException && err.name === "AbortError"`

**Error handling patterns (Python):**
```python
try:
    await db.execute(statement)
except sqlite3.OperationalError as exc:
    if "already exists" not in str(exc):
        logger.debug("Schema statement warning: %s", exc)
```

**Error handling patterns (TypeScript):**
```typescript
if (res.status === 401) {
    localStorage.removeItem("auth_token");
    window.location.href = "/login";
    throw new ApiError(401, "Unauthorized");
}
```

## Logging

**Framework:** Python uses standard `logging` module via `logger = logging.getLogger(__name__)`

**Patterns:**
- All modules define `logger` at top level: `logger = logging.getLogger(__name__)`
- Use `logger.info()` for informational messages (e.g., "Database connected")
- Use `logger.debug()` for verbose details that should not appear in production
- Use `logger.warning()` for recoverable issues
- Use `logger.error()` for failures that need attention
- Format strings with `%s` placeholders: `logger.info("Message: %s", variable)`
- TypeScript: No formal logging setup; console methods implicitly acceptable

**Example:**
```python
logger = logging.getLogger(__name__)
logger.info("Database connected: %s", self._db_path)
logger.debug("Schema statement warning: %s", exc)
```

## Comments

**When to Comment:**
- Explain the "why" for non-obvious logic (e.g., "Don't serve SPA fallback for API routes — let them return proper 404 responses")
- Comment section breaks with `# --- Section Name ---` format (observed in `database.py`, `manager.py`)
- Use `# IMPORTANT:` prefix for critical gotchas (seen in generated docs, adopted in code)
- Comment why workarounds exist (e.g., "Python -m yt_dlp to avoid stale shebang issues")

**Docstrings:**
- Functions: Use triple-quoted docstrings with description and args/returns sections
- Example from `database.py`:
  ```python
  """Open the database connection and apply pragmas."""
  ```
- Example from `manager.py`:
  ```python
  """Initialize the server manager.

  Args:
      registry: DB-backed server registry for persistence.
      config: VastAI configuration.
  """
  ```

**No JSDoc/TSDoc style:**
- TypeScript code does not use formal JSDoc comments; types are self-documenting via TypeScript interfaces

## Function Design

**Size:**
- Python: Functions range from 20-50 lines (typical); longer functions may be split with internal `_` prefixed helpers
- TypeScript: React components and hooks range from 30-100+ lines; utility functions stay under 30 lines

**Parameters:**
- Python: Use type hints with `|` union syntax (e.g., `str | None`, `list[str]`)
- Python: Use `Callable[[Args], ReturnType]` for callbacks
- TypeScript: Use `?` for optional parameters (e.g., `id?: string`) or `| undefined`

**Return values:**
- Python: Explicit return type annotations (e.g., `-> dict[str, int]`, `-> tuple[...] | None`)
- TypeScript: Inferred or explicit (e.g., `Promise<T>`, `UseQueryResult<T>`)

**Async functions:**
- Python: `async def name(...)` → return awaitable; no special suffix
- TypeScript: `async function name(...)` → returns `Promise<T>`; or arrow form: `async () => ...`

## Module Design

**Exports:**
- Python: Modules export classes and functions directly; no explicit `__all__` lists observed
- TypeScript: API client uses object export: `export const api = { ... }` with method chains

**Barrel files:**
- Python: Uses `__init__.py` for package exposure (e.g., `vast_agent/__init__.py`, `trend_parser/__init__.py`)
- TypeScript: No barrel files observed; imports use direct paths (e.g., `import { api } from "./api/client"`)

**Class design:**
- Python: Services and managers use clear separation: `__init__`, public methods, private helpers with `_` prefix
- Example structure from `ServerManager`:
  ```python
  class ServerManager:
      def __init__(self, ...): ...
      # Section: Public API
      def get_server_lock(self, ...): ...
      # Section: Instance discovery
      def discover_instances(self): ...
  ```

**Database access:**
- Avoid direct SQL in routes; use `Database` and `DBServerRegistry` layer abstractions
- Example: `await db.fetchone(...)`, `await db.execute(...)` rather than raw SQL in route handlers

## Validation

**Pydantic patterns:**
- Use `BaseModel` for request/response validation
- Use `Field()` for constraints: `Field(default=..., ge=1, le=200, min_length=1, max_length=128)`
- Use pattern validation: `Field(..., pattern="^(seed|apify|tiktok_custom|instagram_custom)$")`
- Example from `schemas.py`:
  ```python
  class PlatformPipelineConfigIn(BaseModel):
      enabled: bool = True
      source: str = Field(..., pattern="^(seed|apify|tiktok_custom|instagram_custom)$")
      limit: int = Field(default=20, ge=1, le=200)
  ```

**TypeScript validation:**
- No formal schema validator; types document expected shapes
- Frontend uses Pydantic models defined in `types.ts` that match backend

---

*Convention analysis: 2026-03-27*
