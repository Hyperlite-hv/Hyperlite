#!/bin/sh
# Lab probe (#511, 4e): the modes the API is expected to need as www-data, applied before each of its starts. What
# still fails with them is what the probe reports (lab/annotate.py --permissions).
set -u
g=www-data
chgrp "$g" /etc/hyperlite && chmod g+rx /etc/hyperlite
[ -d /etc/hyperlite/local ] && chgrp -R "$g" /etc/hyperlite/local && chmod g+rx /etc/hyperlite/local && chmod g+r /etc/hyperlite/local/*
[ -f /etc/hyperlite/access.db ] && chgrp "$g" /etc/hyperlite/access.db && chmod 0640 /etc/hyperlite/access.db
chgrp "$g" /var/lib/hyperlite && chmod g+rwx /var/lib/hyperlite
mkdir -p /var/lib/hyperlite/tmp && chgrp "$g" /var/lib/hyperlite/tmp && chmod 0770 /var/lib/hyperlite/tmp
for f in /var/lib/hyperlite/hyperlite.db /var/lib/hyperlite/hyperlite.db-wal /var/lib/hyperlite/hyperlite.db-shm; do
    [ -e "$f" ] && chgrp "$g" "$f" && chmod 0660 "$f"
done
mkdir -p /run/lock/hyperlite && chgrp "$g" /run/lock/hyperlite && chmod 0770 /run/lock/hyperlite
exit 0
