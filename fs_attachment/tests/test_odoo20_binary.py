# Copyright 2026 Odestar
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import hashlib
import io
from unittest import mock

from werkzeug.datastructures import FileStorage

from odoo.exceptions import AccessError
from odoo.tools import BinaryValue

from .common import MyException, TestFSAttachmentCommon


class TestOdoo20Binary(TestFSAttachmentCommon):
    def test_adapter_replaced_object_is_collected_after_initial_candidate_clear(self):
        self.temp_backend.use_as_default_for_attachments = True
        attachment = self.ir_attachment_model.create({"name": "gc.bin", "raw": b"old"})
        old_fname = attachment.store_fname
        fs, _, old_path = attachment._fs_parse_store_fname(old_fname)
        self.env.flush_all()
        self.gc_file_model._gc_files_unsafe()
        self.assertFalse(
            self.gc_file_model.search_count([("store_fname", "=", old_fname)])
        )
        with attachment.open("wb") as stream:
            stream.write(b"new")
        self.assertTrue(
            self.gc_file_model.search_count([("store_fname", "=", old_fname)])
        )
        self.env.flush_all()
        self.gc_file_model._gc_files_unsafe()
        self.assertFalse(fs.exists(old_path))
        attachment.invalidate_recordset(["raw"])
        self.assertEqual(attachment.raw.content, b"new")

    def setUp(self):
        super().setUp()
        self.temp_backend.use_as_default_for_attachments = True

    def test_lazy_binary_and_seekable_stream(self):
        attachment = self.ir_attachment_model.create(
            {"name": "stream.bin", "raw": b"seekable payload"}
        )
        attachment.invalidate_recordset(["raw"])
        value = attachment.raw
        self.assertIsInstance(value, BinaryValue)
        self.assertEqual(value.size, 16)
        self.assertEqual(value.filename, "stream.bin")
        with mock.patch.object(value._fs, "open", wraps=value._fs.open) as open_file:
            self.assertEqual(value.size, 16)
            open_file.assert_not_called()
            with value.open() as stream:
                stream.seek(9)
                self.assertEqual(stream.read(), b"payload")
        self.assertEqual(value.content, b"seekable payload")

    def test_name_change_rollback_preserves_original(self):
        attachment = self.ir_attachment_model.create(
            {"name": "original.txt", "raw": b"must survive rollback"}
        )
        fname = attachment.store_fname
        with self.assertRaises(MyException), self.env.cr.savepoint():
            attachment.name = "renamed.txt"
            self.assertEqual(attachment.raw.content, b"must survive rollback")
            raise MyException()
        attachment.invalidate_recordset(["raw"])
        self.assertEqual(attachment.store_fname, fname)
        self.assertEqual(attachment.raw.content, b"must survive rollback")

    def test_stream_write_collision_preserves_object(self):
        self.temp_backend.use_filename_obfuscation = True
        attachment = self.ir_attachment_model.create(
            {"name": "collision.bin", "raw": b"original"}
        )
        with self.assertRaises(RuntimeError):
            attachment._file_write(attachment.store_fname, io.BytesIO(b"different"))
        attachment.invalidate_recordset(["raw"])
        self.assertEqual(attachment.raw.content, b"original")

    def test_attachment_open_checks_access(self):
        attachment = self.ir_attachment_model.create(
            {"name": "private.txt", "raw": b"private"}
        )
        with self.assertRaises(AccessError):
            attachment.with_user(self.demo_user).open("rb")

    def test_empty_payload_uses_binary_value(self):
        attachment = self.ir_attachment_model.create({"name": "empty.bin", "raw": b""})
        self.assertIsInstance(attachment.raw, BinaryValue)
        self.assertEqual(attachment.raw.content, b"")
        self.assertEqual(attachment.raw.size, 0)

    def test_core_upload_routes_to_storage(self):
        payload = b"streamed upload" * 100
        upload = FileStorage(io.BytesIO(payload), filename="upload.txt")
        attachment = self.ir_attachment_model._upload_file(
            upload, {"name": "upload.txt"}
        )
        self.assertEqual(attachment.fs_storage_id, self.temp_backend)
        self.assertEqual(attachment.raw.content, payload)
        self.assertEqual(attachment.checksum, hashlib.sha1(payload).hexdigest())

    def test_core_upload_honors_database_rule(self):
        self.temp_backend.force_db_for_default_attachment_rules = '{"text/plain": 0}'
        upload = FileStorage(io.BytesIO(b"small upload"), filename="upload.txt")
        attachment = self.ir_attachment_model._upload_file(
            upload, {"name": "upload.txt"}
        )
        self.assertFalse(attachment.store_fname)
        self.assertEqual(attachment.db_datas.content, b"small upload")

    def test_gc_retries_failed_delete(self):
        attachment = self.ir_attachment_model.create(
            {"name": "retry.bin", "raw": b"retry"}
        )
        fname = attachment.store_fname
        fs, _, path = attachment._fs_parse_store_fname(fname)
        attachment.unlink()
        with mock.patch.object(fs, "rm", side_effect=OSError("temporary outage")):
            self.gc_file_model._gc_files_unsafe()
        self.assertTrue(fs.exists(path))
        self.assertTrue(self.gc_file_model.search_count([("store_fname", "=", fname)]))
        self.gc_file_model._gc_files_unsafe()
        self.assertFalse(fs.exists(path))
        self.assertFalse(self.gc_file_model.search_count([("store_fname", "=", fname)]))

    def test_update_mode_preserves_tail_and_sha1(self):
        attachment = self.ir_attachment_model.create(
            {"name": "update.bin", "raw": b"abcdef"}
        )
        fname = attachment.store_fname
        with attachment.open("r+b") as stream:
            stream.seek(2)
            stream.write(b"XY")
        self.assertEqual(attachment.raw.content, b"abXYef")
        self.assertEqual(attachment.checksum, hashlib.sha1(b"abXYef").hexdigest())
        self.assertEqual(attachment.file_size, 6)
        self.assertNotEqual(attachment.store_fname, fname)

    def test_database_forced_attachment_adapter(self):
        self.temp_backend.force_db_for_default_attachment_rules = '{"text/plain": 0}'
        attachment = self.ir_attachment_model.create(
            {"name": "db.txt", "raw": b"abcdef"}
        )
        self.assertFalse(attachment.store_fname)
        with attachment.open("r+b") as stream:
            stream.seek(2)
            stream.write(b"XY")
        self.assertEqual(attachment.raw.content, b"abXYef")
        self.assertFalse(attachment.store_fname)
