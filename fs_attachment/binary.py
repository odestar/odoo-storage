# Copyright 2026 Odestar
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Odoo 20 lazy binary values backed by an OCA filesystem."""

from odoo.tools import BinaryValue


class FSBinaryValue(BinaryValue):
    """Snapshot the location, keeping stream reads lazy until content is requested."""

    def __init__(self, attachment):
        self._fs, _storage, self._path = attachment._get_fs_parts()
        self._size = attachment.file_size
        self._filename = attachment.name
        self._mimetype = attachment.mimetype
        self._checksum = attachment.checksum
        self._content = None

    def open(self):
        if self._content is not None:
            return super().open()
        return self._fs.open(self._path, "rb")

    @property
    def content(self):
        if self._content is None:
            with self.open() as stream:
                self._content = stream.read()
        return self._content

    @property
    def size(self):
        return self._size

    @property
    def filename(self):
        return self._filename

    @property
    def mimetype(self):
        return self._mimetype

    @property
    def checksum(self):
        return self._checksum

    def __bool__(self):
        return bool(self._size)
