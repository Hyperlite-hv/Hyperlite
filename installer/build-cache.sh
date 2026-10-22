# Sourced by installer/build-runtime.sh and installer/test-package-inside.sh, in a Debian container of the CI (job
# package): when HYPERLITE_BUILD_CACHE names a directory kept between runs, apt and pip keep what they download there,
# and the next run downloads only what changed. Without the variable (a publication, a development machine), nothing
# changes.
#
# apt installs a .deb it finds in its archive directory WITHOUT checking it against the index (checked: a kept file
# only fails if it is corrupt, a valid one is installed whatever it is). So apt_trust_kept_debs, after `apt-get
# update`, keeps a .deb only when its SHA-256 is the one the signed index just fetched gives for that package and
# version; any other file is deleted and fetched again. pip checks every file it uses against the hashes of
# requirements.txt itself.
if [ -n "${HYPERLITE_BUILD_CACHE:-}" ]; then
    # The Debian images delete each .deb after installing it.
    rm -f /etc/apt/apt.conf.d/docker-clean
    mkdir -p "$HYPERLITE_BUILD_CACHE/apt/partial" "$HYPERLITE_BUILD_CACHE/pip"
    echo "Dir::Cache::Archives \"$HYPERLITE_BUILD_CACHE/apt\";" > /etc/apt/apt.conf.d/90hyperlite-build-cache
    export PIP_CACHE_DIR="$HYPERLITE_BUILD_CACHE/pip"
fi

apt_trust_kept_debs() {
    [ -n "${HYPERLITE_BUILD_CACHE:-}" ] || return 0
    local deb name version expected kept=0 dropped=0
    for deb in "$HYPERLITE_BUILD_CACHE"/apt/*.deb; do
        [ -e "$deb" ] || continue
        name=$(dpkg-deb -f "$deb" Package 2> /dev/null) && version=$(dpkg-deb -f "$deb" Version 2> /dev/null) &&
            expected=$(apt-cache show "$name=$version" 2> /dev/null | sed -n 's/^SHA256: //p' | head -n 1) || expected=
        if [ -n "$expected" ] && [ "$(sha256sum < "$deb" | cut -d' ' -f1)" = "$expected" ]; then
            kept=$((kept + 1))
        else
            rm -f "$deb"
            dropped=$((dropped + 1))
        fi
    done
    echo "[build-cache] kept .deb files: $kept match the signed index, $dropped dropped"
}
