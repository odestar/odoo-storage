# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging
import threading
from contextlib import closing, contextmanager

from odoo import api, fields, models, modules
from odoo.sql_db import Cursor

_logger = logging.getLogger(__name__)


class FsFileGC(models.Model):
    _name = "fs.file.gc"
    _description = "Filesystem storage file garbage collector"

    store_fname = fields.Char("Stored Filename")
    fs_storage_code = fields.Char("Storage Code")

    _store_fname_uniq = models.Constraint(
        "unique (store_fname)",
        "The stored filename must be unique!",
    )

    def _is_test_mode(self) -> bool:
        """Return True if we are running the tests, so we do not mark files for
        garbage collection into a separate transaction.
        """
        return (
            getattr(threading.current_thread(), "testing", False)
            or modules.module.current_test
        )

    @contextmanager
    def _in_new_cursor(self) -> Cursor:
        """Context manager to execute code in a new cursor"""
        if self._is_test_mode() or not self.env.registry.ready:
            yield self.env.cr
            return

        with closing(self.env.registry.cursor()) as cr:
            try:
                yield cr
            except Exception:
                cr.rollback()
                raise
            else:
                # disable pylint error because this is a valid commit,
                # we are in a new env
                cr.commit()  # pylint: disable=invalid-commit

    @api.model
    def _mark_for_gc(self, store_fname: str) -> None:
        """Mark a file for garbage collection"

        This process is done in a separate transaction since the data must be
        preserved even if the transaction is rolled back.
        """
        with self._in_new_cursor() as cr:
            code = store_fname.partition("://")[0]
            # use plain SQL to avoid the ORM ignore conflicts errors
            cr.execute(
                """
                INSERT INTO
                    fs_file_gc (
                        store_fname,
                        fs_storage_code,
                        create_date,
                        write_date,
                        create_uid,
                        write_uid
                    )
                    VALUES (
                        %s,
                        %s,
                        now() at time zone 'UTC',
                        now() at time zone 'UTC',
                        %s,
                        %s
                    )
                ON CONFLICT DO NOTHING
            """,
                (store_fname, code, self.env.uid, self.env.uid),
            )

    @api.autovacuum
    def _gc_files(self) -> None:
        """Garbage collect files"""
        # Odoo 20 resets its transaction after commit by querying registry
        # signaling. That query opens a REPEATABLE READ snapshot, so reusing
        # the caller cursor can miss an attachment committed while LOCK waits.
        self.env.cr.commit()  # pylint: disable=invalid-commit
        with self.env.registry.cursor() as cr:
            # Lock before constructing an Environment: its initialization also
            # queries signaling. The first snapshot must follow the writer.
            cr.execute("SET LOCAL lock_timeout TO '10s'")
            cr.execute("LOCK ir_attachment IN SHARE MODE")
            # Serialize collectors so their candidate DELETEs cannot deadlock.
            cr.execute("LOCK fs_file_gc IN SHARE ROW EXCLUSIVE MODE")
            env = api.Environment(cr, self.env.uid, self.env.context, su=self.env.su)
            env["fs.file.gc"]._gc_files_unsafe()

    def _gc_files_unsafe(self) -> None:
        # get the list of fs.storage codes that must be autovacuumed
        codes = (
            self.env["fs.storage"].search([]).filtered("autovacuum_gc").mapped("code")  # pylint: disable=no-search-all
        )
        if not codes:
            return
        # we process by batch of storage codes.
        self.env.cr.execute(
            """
            SELECT
                fs_storage_code,
                array_agg(store_fname)

            FROM
                fs_file_gc
            WHERE
                fs_storage_code IN %s
                AND NOT EXISTS (
                    SELECT 1
                    FROM ir_attachment
                    WHERE store_fname = fs_file_gc.store_fname
                )
            GROUP BY
                fs_storage_code
            """,
            (tuple(codes),),
        )
        failed = []
        for code, store_fnames in self.env.cr.fetchall():
            self.env["fs.storage"].get_by_code(code)
            fs = self.env["fs.storage"].get_fs_by_code(code)
            for store_fname in store_fnames:
                try:
                    file_path = store_fname.partition("://")[2]
                    fs.rm(file_path)
                except FileNotFoundError:
                    pass
                except Exception:
                    failed.append(store_fname)
                    _logger.debug("Failed to remove file %s", store_fname)

        # delete the records from the table fs_file_gc
        self.env.cr.execute(
            """
            DELETE FROM
                fs_file_gc
            WHERE
                fs_storage_code IN %s
                AND NOT (store_fname = ANY(%s))
            """,
            (tuple(codes), failed),
        )
