# Odestar Odoo 20 storage port

This branch maintains `fs_storage` and `fs_attachment` for Odoo 20. It starts
from OCA/storage PR #670, commit
`cf8c2fe8aeef687c206fcbc656d8a53571c2c281`. Upstream history and module licenses
are preserved. The other addons in this repository are not part of this port.

The patch adapts Odoo 20's lazy binary values, streamed upload entry point,
access-control records, HTTP streams, configuration APIs and cache invalidation.
It retains the OCA filesystem abstraction and storage configuration. Additional
regressions cover rollback-safe object names, garbage-collection retries,
attachment permissions, SHA1 metadata, range/conditional responses, and file
adapter writes and close behavior. Local filestore updates go through Odoo's
binary inverse because Odoo 20 makes local objects immutable.

## Image integration

Build `docker/Dockerfile` against an authorized Odoo 20 source image containing
`/opt/venv` and `/opt/odoo`. Supply its immutable reference as `ODOO_BASE_IMAGE`.
The modules are installed in `/mnt/extra-addons/platform`, which must appear in
`addons_path`. The Python lock is installed with `--require-hashes`.
Keep images containing Enterprise source in private storage.

Regenerate `docker/requirements.lock` from `docker/requirements.txt` using
pip-tools 7.5.1 with `pip-compile --generate-hashes --no-emit-index-url
--no-emit-trusted-host`. Pin the fork's full commit and both manifest hashes in
the deployment platform; do not deploy a moving branch reference.

## Validation

On 2026-10-11, the remote Odoo `20.0+e-20261003` fixture passed 112 actual test
methods selected by `/fs_attachment,/fs_storage,-/web`, including inherited
adapter cases. Both modules were installed with `web_enterprise`. Ruff formatting
and lint checks passed. The same fixture used a separate PostgreSQL 17 server
with certificate verification and real R2 object storage. Upload/read/hash,
empty content, 1 MB and 6 MB payloads, update/rename rollback, private read denial,
and garbage-collection outage/retry checks passed. Browser upload, authenticated
and ranged downloads, and anonymous-access denial also passed.

Odoo 20 filename onchange could discard a browser upload; the raw field's
explicit dependency list excludes filename and persisted renames invalidate its
cached binary value. GC uses a fresh cursor and locks before constructing an
Environment because Odoo 20 transaction signaling otherwise starts a stale
snapshot. `docker/test_gc_concurrency.py` reproduces a waiting writer with two
collectors and verifies that only the obsolete object is collected. It passed
against remote PostgreSQL and R2; run it only on a disposable database outside
the rollback-only module test harness.

Module tests are not platform recovery certification. Platform admission remains
owned by the deployment and backup repositories and their live recovery evidence.
Redis sessions, K3s, and the other OCA addons are outside this port's scope.
