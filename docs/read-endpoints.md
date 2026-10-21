# EP Read Endpoints

EP exposes read-only HTTP endpoints for inspecting the resources it manages. These are distinct from the command system, which handles all state mutations via `POST /v1/commands`. Reads are proxied through Syntara (AO), which enforces access control and surfaces a curated subset of EP fields at `/api/execution_plane/v1/`.

## Resources exposed via the AO facade

### Work Items

AO endpoint: `GET /api/execution_plane/v1/work_items`

| Field | Type |
|---|---|
| `id` | UUID |
| `status` | string |
| `result` | object \| null |
| `created_at` | datetime |
| `claimed_at` | datetime \| null |
| `completed_at` | datetime \| null |

Work item statuses: `pending`, `claimed`, `dispatched`, `cancel_requested`, `reconciliation_required`, `completed`, `failed`, `cancelled`.

### Execution Targets

AO endpoint: `GET /api/execution_plane/v1/execution_targets`

| Field | Type |
|---|---|
| `id` | UUID |
| `cluster_id` | UUID |
| `name` | string |
| `backend_type` | string |
| `placement` | `{"type": "kubernetes", ...}` or `{"type": "rhel"}` |
| `status` | string |
| `enabled` | bool |
| `is_default` | bool |
| `labels` | `{string: string}` |
| `created_at` | datetime |
| `last_ran_at` | datetime \| null |

EP also has a `/v1/clusters` endpoint that is not currently proxied through AO.
Cluster reads include the binding's `is_default` desired-state field.

## Pagination

EP list endpoints mirror Syntara's own pagination contract — AO passes EP cursors through unchanged. See the [AO API response format standard](https://github.com/syntara-orchestration/syntara/blob/devel/backend/docs/standards/api-response-format.md) for cursor structure, stable ordering guarantees, and the N+1 fetch pattern.

Query parameters: `limit` (1–100, default 20), `cursor`, `sort` (`field` or `-field` for descending), `include_total`.

Work items default to `-created_at` (newest first). Sortable fields are `created_at`, `status`, `resource_cleanup_status`, and `id`. Nullable timestamps `claimed_at` and `completed_at` are not sortable.

Clusters and execution targets default to `created_at` (oldest first) and accept only `created_at` or `-created_at`. Unsupported sorts return HTTP 422.

Follow `next` or `prev` to navigate in either direction. Keep the same `sort` and `limit` when following a cursor; its sort field and direction must match the requested sort (including the endpoint default when `sort` is omitted). A missing page in either direction is represented by a null cursor. Totals are computed only when `include_total=true`.

Cursors are base64-encoded JSON objects containing string fields `id`, `created_at`, `direction`, `sort_field`, `sort_direction`, and `sort_value`. Invalid or incomplete cursors, including timestamps without a timezone, return HTTP 422. Unknown cursor fields are ignored.

```
GET /api/execution_plane/v1/work_items?limit=50&sort=status
GET /api/execution_plane/v1/work_items?limit=20&cursor=<token from previous response>
```

## OpenAPI spec

The read endpoints described here and the command ingestion endpoint (`POST /v1/commands`) share a single spec file committed at [`openapi.yaml`](../openapi.yaml) — there is no separate spec for reads vs. commands. The spec is generated automatically by FastAPI from route decorators and Pydantic type annotations.
