# Copyright 2026 Odestar
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Run against a disposable configured DB with default remote storage and GC enabled.

Usage: python docker/test_gc_concurrency.py -c /path/odoo.conf -d disposable_db
Uses the storage's configured environment credentials. It commits test records;
run outside Odoo's rollback-only module-test harness. No credentials are printed.
"""

import argparse
import json
import threading
import time
import uuid

from odoo import SUPERUSER_ID, api
from odoo.modules.registry import Registry
from odoo.tools import BinaryBytes, config

parser = argparse.ArgumentParser()
parser.add_argument("-c", "--config", required=True)
parser.add_argument("-d", "--database", required=True)
args = parser.parse_args()
config.parse_config(["-c", args.config, "--no-http", "--max-cron-threads", "0"])
registry = Registry(args.database)
errors = []
started = threading.Event()
with registry.cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    attachment = env["ir.attachment"].create(
        {
            "name": "concurrent-" + uuid.uuid4().hex + ".bin",
            "raw": BinaryBytes(b"old concurrent bytes"),
        }
    )
    assert attachment.fs_storage_id.autovacuum_gc
    old_fname = attachment.store_fname
    fs, _, old_path = attachment._fs_parse_store_fname(old_fname)
    cr.commit()
    env["fs.file.gc"]._gc_files()
    cr.execute("SELECT pg_backend_pid()")
    writer_pid = cr.fetchone()[0]
    attachment.write({"raw": BinaryBytes(b"new concurrent bytes")})
    env.flush_all()
    new_fname = attachment.store_fname
    _, _, new_path = attachment._fs_parse_store_fname(new_fname)

    def collect():
        try:
            with registry.cursor() as other:
                started.set()
                api.Environment(other, SUPERUSER_ID, {})["fs.file.gc"]._gc_files()
        except BaseException as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=collect) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert started.wait(10)
    locked = False
    for _ in range(30):
        cr.execute("SELECT pg_stat_clear_snapshot()")
        cr.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
            "AND %s = ANY(pg_blocking_pids(pid))",
            (writer_pid,),
        )
        rows = cr.fetchall()
        if len(rows) == 2:
            locked = True
            break
        time.sleep(0.1)
    assert locked, "garbage collector did not wait for attachment transaction"
    cr.commit()
    for thread in threads:
        thread.join(20)
        assert not thread.is_alive()
    assert not errors, errors
    with registry.cursor() as observer:
        fresh = api.Environment(observer, SUPERUSER_ID, {})
        current = fresh["ir.attachment"].browse(attachment.id)
        assert current.store_fname == new_fname
        assert not fresh["fs.file.gc"].search_count(
            [("store_fname", "in", [old_fname, new_fname])]
        )
        try:
            old_info = fs.info(old_path, refresh=True)
        except FileNotFoundError:
            old_info = None
        assert old_info is None, old_info
        assert fs.info(new_path, refresh=True)["size"] == len(b"new concurrent bytes")
        assert current.raw.content == b"new concurrent bytes"
        current.unlink()
        observer.commit()
        fresh["fs.file.gc"]._gc_files()
        assert not fs.exists(new_path)
        print(
            "CONCURRENT_GC_RESULT",
            json.dumps(
                {
                    "writer_fenced_gc": locked,
                    "old_collected": True,
                    "new_preserved": True,
                    "attachment_id": attachment.id,
                }
            ),
        )
    cr.commit()
