#!/bin/sh
# Run by d-i in preseed/late_command (preseed.cfg), in the installer. It asks its questions through the
# installer's own screens, in custom mode only (hlmode=custom on the kernel command line, see build-iso.sh).
#
#   keyboard  (both modes) writes /target/etc/default/keyboard from the keyboard chosen in the installer, before
#         postinstall.sh installs keyboard-configuration, which keeps it. The installer's own step installs it from
#         the CD, which has no usable APT source without a network mirror, so it fails.
#   ask   the password of the dashboard's admin account and, optionally, the alerts by email. The answers go to
#         /target/root/hyperlite-installer/answers.env (root only), which postinstall.sh reads; the installer
#         files are removed at the end of postinstall.sh.
#   done  how to reach the dashboard, once postinstall.sh is done (it leaves /target/root/.hyperlite-install-result).
set -e

. /usr/share/debconf/confmodule

if [ "${1:-}" = keyboard ]; then
    db_get keyboard-configuration/xkb-keymap || true
    keymap=$RET
    # No answer (the question was never asked): keep Debian's default rather than guess.
    [ -n "$keymap" ] || exit 0
    # A keymap is "layout" or "layout(variant)", for example "fr" or "fr(latin9)".
    layout=${keymap%%(*}
    variant=""
    case "$keymap" in *"("*")") variant=${keymap#*(}; variant=${variant%)} ;; esac
    mkdir -p /target/etc/default
    cat > /target/etc/default/keyboard <<KBEOF
XKBMODEL="pc105"
XKBLAYOUT="$layout"
XKBVARIANT="$variant"
XKBOPTIONS=""
BACKSPACE="guess"
KBEOF
    exit 0
fi

grep -q 'hlmode=custom' /proc/cmdline || exit 0

debconf-loadtemplate hyperlite "$(dirname "$0")/hyperlite.templates"

ANSWERS=/target/root/hyperlite-installer/answers.env
RESULT=/target/root/.hyperlite-install-result
db_get debian-installer/language || true
LANG_CODE=${RET:-en}

# say FRENCH ENGLISH: the sentence in the installer's language.
say() { if [ "$LANG_CODE" = fr ]; then echo "$1"; else echo "$2"; fi; }

ask() {
    db_fset "$1" seen false
    db_input critical "$1" || true
    db_go || true
    db_get "$1"
}

error() {
    db_subst "$1" REASON "$2"
    db_fset "$1" seen false
    db_input critical "$1" || true
    db_go || true
}

# The rules the installer can check by itself; postinstall.sh then checks the password against Hyperlite's own
# policy (common passwords, sequences, the account name).
weakness() {
    bytes=$(printf '%s' "$1" | wc -c)
    # Characters (not bytes) and distinct characters; the installer's busybox has no "wc -m".
    set -- $(printf '%s\n' "$1" | awk '{ n = split($0, c, ""); for (i = 1; i <= n; i++) seen[c[i]] = 1; k = 0; for (x in seen) k++; print n, k }')
    length=$1
    distinct=$2
    if [ "$length" -lt 12 ]; then
        say "Il faut au moins 12 caractères (il y en a $length)." "It needs 12 characters at least (it has $length)."
    elif [ "$bytes" -gt 72 ]; then
        say "Il est trop long (72 octets au plus)." "It is too long (72 bytes at most)."
    elif [ "$distinct" -lt 5 ]; then
        say "Il faut au moins 5 caractères différents." "It needs 5 different characters at least."
    fi
}

# A value for answers.env, between single quotes.
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

case "${1:-}" in
ask)
    while :; do
        ask hyperlite/admin-password; p1=$RET
        ask hyperlite/admin-password-again; p2=$RET
        if [ "$p1" != "$p2" ]; then
            error hyperlite/password-mismatch ""
            continue
        fi
        reason=$(weakness "$p1")
        if [ -n "$reason" ]; then
            error hyperlite/password-weak "$reason"
            continue
        fi
        break
    done
    # The installer keeps its answers in its database and its logs: the passwords are not left there.
    db_set hyperlite/admin-password ""
    db_set hyperlite/admin-password-again ""

    to=""; host=""; port=""; user=""; pass=""
    ask hyperlite/email-alerts
    if [ "$RET" = true ]; then
        while :; do
            ask hyperlite/email-to; to=$RET
            case "$to" in
                *@*.*) case "$to" in *" "*) ;; *) break ;; esac ;;
            esac
            error hyperlite/invalid-value "$(say "« $to » n'est pas une adresse e-mail." "\"$to\" is not an email address.")"
        done
        while :; do
            ask hyperlite/smtp-host; host=$RET
            case "$host" in "" | *" "*) ;; *) break ;; esac
            error hyperlite/invalid-value "$(say "Indiquez le nom ou l'adresse du serveur SMTP." "Give the name or address of the SMTP server.")"
        done
        while :; do
            ask hyperlite/smtp-port; port=$RET
            case "$port" in
                "" | *[!0-9]*) ;;
                *) [ "$port" -ge 1 ] && [ "$port" -le 65535 ] && break ;;
            esac
            error hyperlite/invalid-value "$(say "Le port est un nombre de 1 à 65535." "The port is a number from 1 to 65535.")"
        done
        ask hyperlite/smtp-user; user=$RET
        if [ -n "$user" ]; then
            ask hyperlite/smtp-password; pass=$RET
            db_set hyperlite/smtp-password ""
        fi
    fi

    umask 077
    {
        echo "HL_ADMIN_PASSWORD=$(quote "$p1")"
        echo "HL_ALERT_TO=$(quote "$to")"
        echo "HL_SMTP_HOST=$(quote "$host")"
        echo "HL_SMTP_PORT=$(quote "$port")"
        echo "HL_SMTP_USER=$(quote "$user")"
        echo "HL_SMTP_PASSWORD=$(quote "$pass")"
    } > "$ANSWERS"
    ;;
done)
    ip=$(ip -4 addr show scope global 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | head -n 1)
    url="https://${ip:-$(say "<adresse du serveur>" "<server address>")}:8000"
    admin=""; reason=""
    # shellcheck disable=SC1090
    [ -f "$RESULT" ] && . "$RESULT"
    rm -f "$RESULT"
    if [ "$admin" = chosen ]; then
        password=$(say "Mot de passe : celui que vous avez choisi." "Password: the one you chose.")
    else
        initial=$(cat /target/root/.hyperlite-initial-password 2>/dev/null || true)
        password=$(say "Votre mot de passe a été refusé ($reason) : utilisez le mot de passe initial $initial, affiché aussi sur la console, et changez-le à la première connexion." \
            "Your password was refused ($reason): use the initial password $initial, also shown on the console, and change it when you first sign in.")
    fi
    db_subst hyperlite/finished URL "$url"
    db_subst hyperlite/finished PASSWORD "$password"
    db_fset hyperlite/finished seen false
    db_input critical hyperlite/finished || true
    db_go || true
    ;;
esac
