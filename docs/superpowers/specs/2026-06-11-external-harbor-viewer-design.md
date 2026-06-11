# External Harbor Viewer Design

## Goal

AEO must stop starting Harbor Viewer processes when the user clicks Harbor Viewer buttons. Users can run Harbor Viewer themselves and point AEO at that existing viewer. If no viewer is configured or the configured viewer is not healthy, AEO returns an error instead of spawning a process.

## Configuration

Add one controller environment variable:

```bash
AEO_HARBOR_VIEWER_URL=http://127.0.0.1:7369
```

The value is a full base URL for an already running Harbor Viewer. A full URL is preferred over separate host and port fields because it supports local ports, remote IPs, and reverse proxy URLs without additional settings.

## Runtime Behavior

The global Harbor Viewer and batch Harbor Viewer buttons keep their current front-end flow: click, POST to the controller, then open the returned URL in a popup. The controller behavior changes:

1. Rebuild or normalize local Harbor job data when that is already part of the current endpoint behavior.
2. Read `AEO_HARBOR_VIEWER_URL`.
3. If the variable is empty, return `available: false` with a message telling the user to configure the URL and start Harbor Viewer manually.
4. If configured, call `<AEO_HARBOR_VIEWER_URL>/api/health` with a short timeout.
5. If healthy, return the configured URL to the front end.
6. If unhealthy, return `available: false` with the failed URL and reason.

AEO does not call `subprocess.Popen` for Harbor Viewer in either global or batch endpoints.

## Manual Startup

The expected manual command remains outside AEO, for example:

```bash
cd /home/djn/code/harbor
uv run harbor view /home/djn/code/harbor/jobs --host 0.0.0.0 --port 7369 --no-build
```

The configured URL should point to the host and port exposed by that command.

## Code Changes

Update controller configuration and `.env.example` to document `AEO_HARBOR_VIEWER_URL`.

Replace automatic viewer creation in:

- Global viewer endpoint: `_ensure_global_harbor_viewer`
- Batch viewer endpoint: `/api/batches/{batchId}/viewer`

Keep existing route responses compatible with the front end by returning `available`, `url`, `embeddedUrl` where applicable, `jobsDir`, and `harborRepo`.

The existing `HarborViewerManager` process-spawning implementation can either be removed from request paths or left unused for now. Request handling must not call `ensure_viewer`.

## Error Handling

Missing config returns an actionable message:

```text
Harbor Viewer 未配置，请设置 AEO_HARBOR_VIEWER_URL 并手动启动 harbor view
```

Health check failure returns an actionable message containing the configured URL and the failure reason. The front end already displays `info.reason`, so no UI redesign is required.

## Testing

Add controller tests for:

- Missing `AEO_HARBOR_VIEWER_URL` returns `available: false` and does not spawn a process.
- Configured healthy viewer returns `available: true` and the configured URL.
- Configured unhealthy viewer returns `available: false`.

Patch process-spawning calls in tests so any accidental `subprocess.Popen` usage fails the test.
