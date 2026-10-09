# Return to the version before these changes

A complete archive was created before editing repository files:

/workspace/backups/legal-case-manager/20261009T162928Z/checkout-before-octop.tar.gz

The archive includes the original files and Git metadata at commit 848bc145d686fe9e6b7b4764f8648dabe5655479. All 31 archived files were compared with the originals. A SHA256SUMS file and RESTORE.md are beside the archive.

Recover into another directory so later work is retained:

    cd /workspace/backups/legal-case-manager/20261009T162928Z
    sha256sum --check SHA256SUMS
    mkdir /workspace/legal-case-manager-restored
    tar -xzf checkout-before-octop.tar.gz -C /workspace/legal-case-manager-restored

Use /workspace/legal-case-manager-restored/legal-case-manager as the previous checkout. Stop the current workspace before switching. Preserve its data/ directory if you have since uploaded documents; those later documents are not part of the earlier backup.

Copy the backup archive somewhere durable before replacing or discarding the cloud environment. Do not rely on this path being available on an unrelated machine.
