# Supply an immutable Odoo 20 image reference from your private registry.
ARG ODOO_BASE_IMAGE
FROM ${ODOO_BASE_IMAGE}
USER root
COPY docker/requirements.lock /tmp/odestar-storage-requirements.lock
RUN /opt/venv/bin/python -m pip install --no-cache-dir --require-hashes -r /tmp/odestar-storage-requirements.lock \
    && /opt/venv/bin/python -c "from odoo import release; assert release.version_info[:2] == (20, 0)"
COPY --chown=1001:1001 fs_storage /mnt/extra-addons/platform/fs_storage
COPY --chown=1001:1001 fs_attachment /mnt/extra-addons/platform/fs_attachment
USER 1001:1001
