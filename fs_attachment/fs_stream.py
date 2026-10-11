# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from __future__ import annotations

from odoo.http import request
from odoo.http.response import Response
from odoo.http.stream import STATIC_CACHE_LONG, Stream

from .models.ir_attachment import IrAttachment

try:
    from werkzeug.utils import secure_filename
    from werkzeug.utils import send_file as _send_file
except ImportError:
    from odoo.tools._vendor.send_file import send_file as _send_file


class FsStream(Stream):
    fs_attachment = None

    def __init__(self, **kwargs):
        # Odoo20 validates the built-in types before custom types can extend them.
        is_fs = kwargs.get("type") == "fs"
        if is_fs:
            kwargs.update(type="data", data=b"")
        super().__init__(**kwargs)
        if is_fs:
            self.type = "fs"

    @classmethod
    def from_fs_attachment(cls, attachment: IrAttachment) -> FsStream:
        attachment.ensure_one()
        if not attachment.fs_filename:
            raise ValueError("Attachment is not stored into a filesystem storage")
        return cls(
            mimetype=attachment.mimetype,
            download_name=attachment.name,
            conditional=True,
            public=attachment.public,
            etag=attachment.checksum,
            type="fs",
            size=attachment.file_size,
            last_modified=attachment["write_date"],
            fs_attachment=attachment,
        )

    def read(self):
        if self.type == "fs":
            with self.fs_attachment.open("rb") as f:
                return f.read()
        return super().read()

    def get_response(
        self,
        as_attachment=None,
        immutable=None,
        content_security_policy="default-src 'none'",
        **send_file_kwargs,
    ):
        if self.type != "fs":
            return super().get_response(
                as_attachment=as_attachment,
                immutable=immutable,
                content_security_policy=content_security_policy,
                **send_file_kwargs,
            )
        if as_attachment is None:
            as_attachment = self.as_attachment
        if immutable is None:
            immutable = self.immutable
        # Sanitize the download_name before passing it
        safe_download_name = secure_filename(self.download_name or "")
        send_file_kwargs = {
            "mimetype": self.mimetype,
            "as_attachment": as_attachment,
            "download_name": safe_download_name,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "max_age": STATIC_CACHE_LONG if immutable else self.max_age,
            "environ": request.httprequest.environ,
            "response_class": Response,
            **send_file_kwargs,
        }
        conditional = send_file_kwargs.pop("conditional", self.conditional)
        send_file_kwargs["conditional"] = False
        use_x_sendfile = self.fs_attachment._fs_use_x_sendfile()
        # The file will be closed by werkzeug...
        send_file_kwargs["use_x_sendfile"] = use_x_sendfile
        if not use_x_sendfile:
            f = self.fs_attachment.open("rb")
            try:
                res = _send_file(f, **send_file_kwargs)
                res.content_length = self.size
                if conditional:
                    res.make_conditional(
                        request.httprequest.environ,
                        accept_ranges=True,
                        complete_length=self.size,
                    )
                res.call_on_close(f.close)
            except Exception:
                f.close()
                raise
        else:
            x_sendfile_path = self.fs_attachment._get_x_sendfile_path()
            send_file_kwargs["use_x_sendfile"] = True
            res = _send_file("", **send_file_kwargs)
            # nginx specific headers
            res.headers["X-Accel-Redirect"] = x_sendfile_path
            # apache specific headers
            res.headers["X-Sendfile"] = x_sendfile_path
            res.headers["Content-Length"] = 0

        public = bool(self.public and (send_file_kwargs.get("max_age") or 0) > 0)
        res.cache_control.public = public
        res.cache_control.private = not public
        if immutable and res.cache_control:
            res.cache_control["immutable"] = None

        res.headers["X-Content-Type-Options"] = "nosniff"

        if content_security_policy:
            res.headers["Content-Security-Policy"] = content_security_policy

        return res
