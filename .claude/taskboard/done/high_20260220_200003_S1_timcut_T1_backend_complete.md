REQUESTED_BY: S1
TASK: Complete the TimCut backend — implement all API route handlers, Celery task wrappers, storage/asset management, and WebSocket job progress. The skeleton exists at /home/darren/Desktop/timcut/backend/ with stub route files and Celery config already written.

Work items:

1. api/routes/upload.py — Implement:
   - POST / : Accept file upload (video/audio/image), save to /storage/uploads/{uuid}.{ext}, generate thumbnail via ffprobe+ffmpeg, store metadata in SQLite, return asset_id
   - POST /batch : Accept multiple files, process each
   - Use chunked upload for large files (stream to disk, don't buffer in RAM)

2. api/routes/assets.py — Implement:
   - GET / : List assets with pagination (offset/limit), optional type filter (video/audio/image)
   - GET /{asset_id} : Return full ffprobe metadata
   - GET /{asset_id}/thumbnail : Serve thumbnail image
   - DELETE /{asset_id} : Remove from DB + disk
   - PUT /{asset_id}/tags : Update asset tags

3. api/routes/process.py — Implement ALL processing endpoints. Each one:
   - Accepts input asset_id + parameters
   - Validates the asset exists
   - Dispatches a Celery task
   - Returns {job_id} immediately
   - Processing endpoints: trim, concat, convert, resize, speed, overlay/text, overlay/image, color, transition, crop, audio/extract, audio/replace, gif, thumbnail

4. api/routes/jobs.py — Implement:
   - GET / : List recent jobs (last 50)
   - GET /{job_id} : Get job status, progress percentage, result
   - DELETE /{job_id} : Cancel running job (celery revoke)
   - WebSocket /ws/progress : Stream progress updates for all active jobs

5. workers/tasks/ — Create task modules that wrap the processor functions:
   - workers/tasks/__init__.py
   - workers/tasks/video.py — Celery tasks for trim, concat, convert, resize, speed, crop, transition, gif, thumbnail
   - workers/tasks/audio.py — Celery tasks for extract, replace, mix
   - workers/tasks/image.py — Celery tasks for overlay, color grading
   - workers/tasks/ml.py — Placeholder Celery tasks for Tier 2 ML features (empty stubs, not implemented)
   - Each task: update progress via self.update_state(), handle errors, clean up temp files

6. storage/asset_db.py — SQLite-based asset metadata store:
   - Table: assets (id TEXT PK, filename TEXT, original_name TEXT, file_type TEXT, mime_type TEXT, size_bytes INT, duration_ms INT, width INT, height INT, fps REAL, codec TEXT, thumbnail_path TEXT, tags TEXT, created_at TEXT, updated_at TEXT)
   - Table: jobs (id TEXT PK, task_type TEXT, status TEXT, progress REAL, input_asset_id TEXT, output_asset_id TEXT, params TEXT, error TEXT, created_at TEXT, completed_at TEXT)
   - CRUD functions for both tables

The existing files to build on:
- api/main.py — Already has auth, CORS, router mounting
- workers/celery_app.py — Already has Celery config
- All route stubs have TODO comments listing required endpoints

SCOPE: /home/darren/Desktop/timcut/backend/
OUTPUT_TO: All files in backend/ directory (api/, workers/, storage/)
PRIORITY: high
CONSTRAINT: Do NOT install packages or run anything. Write Python source only. Processors are being written by another task (T2) — import from processors.* but those files may not exist yet. That's fine.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20T23:08:36Z
COMPLETED_AT: 2026-02-20T23:18:39Z
RESULT: All files written successfully
