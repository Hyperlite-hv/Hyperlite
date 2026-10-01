#include "server.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define MAX_CLIENTS 128
#define MAX_PENDING_OUT (8u * CFS_FRAME_MAX) /* a client that stops reading is dropped */

typedef struct {
    int fd;
    uid_t uid;
    uint8_t *in;
    size_t in_len;
    size_t in_cap;
    uint8_t *out;
    size_t out_len;
    size_t out_off;
    size_t out_cap;
} client;

static void client_close(client *c)
{
    close(c->fd);
    free(c->in);
    free(c->out);
    memset(c, 0, sizeof(*c));
    c->fd = -1;
}

static int set_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int listen_on(const char *path, unsigned mode)
{
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(addr.sun_path)) {
        fprintf(stderr, "hyperlite-cfs: socket path too long: %s\n", path);
        return -1;
    }
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) {
        perror("hyperlite-cfs: socket");
        return -1;
    }
    /* Created with no permission at all, then opened to `mode`: no window where another user could connect. */
    unlink(path);
    mode_t old = umask(0777);
    int rc = bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    umask(old);
    if (rc < 0 || chmod(path, mode) < 0 || listen(fd, 64) < 0) {
        fprintf(stderr, "hyperlite-cfs: cannot listen on %s: %s\n", path, strerror(errno));
        close(fd);
        return -1;
    }
    return fd;
}

static bool reserve(uint8_t **buf, size_t *cap, size_t need)
{
    if (need <= *cap)
        return true;
    size_t n = *cap ? *cap : 4096;
    while (n < need)
        n *= 2;
    uint8_t *p = realloc(*buf, n);
    if (!p)
        return false;
    *buf = p;
    *cap = n;
    return true;
}

/* Handle every complete frame in the input buffer. Returns false when the client must be dropped. */
static bool process_input(cfs_ctx *ctx, client *c)
{
    size_t off = 0;
    while (c->in_len - off >= 4) {
        const uint8_t *h = c->in + off;
        uint32_t len = (uint32_t)h[0] | (uint32_t)h[1] << 8 | (uint32_t)h[2] << 16 | (uint32_t)h[3] << 24;
        if (len > CFS_FRAME_MAX)
            return false; /* nothing sane follows a corrupt length */
        if (c->in_len - off - 4 < len)
            break;
        cfs_writer answer;
        cfs_writer_init(&answer);
        cfs_handle(ctx, h + 4, len, c->uid, time(NULL), &answer);
        bool ok = !answer.err && c->out_len - c->out_off + answer.len <= MAX_PENDING_OUT &&
                  reserve(&c->out, &c->out_cap, c->out_len + answer.len);
        if (ok) {
            memcpy(c->out + c->out_len, answer.p, answer.len);
            c->out_len += answer.len;
        }
        cfs_writer_free(&answer);
        if (!ok)
            return false;
        off += 4 + len;
    }
    if (off) {
        memmove(c->in, c->in + off, c->in_len - off);
        c->in_len -= off;
    }
    return true;
}

static bool read_client(cfs_ctx *ctx, client *c)
{
    for (;;) {
        if (!reserve(&c->in, &c->in_cap, c->in_len + 65536))
            return false;
        ssize_t n = read(c->fd, c->in + c->in_len, c->in_cap - c->in_len);
        if (n > 0) {
            c->in_len += (size_t)n;
            if (!process_input(ctx, c))
                return false;
            if (c->in_len > CFS_FRAME_MAX + 4)
                return false;
            continue;
        }
        if (n == 0)
            return false;
        if (errno == EAGAIN || errno == EWOULDBLOCK)
            return true;
        if (errno == EINTR)
            continue;
        return false;
    }
}

static bool write_client(client *c)
{
    while (c->out_off < c->out_len) {
        ssize_t n = write(c->fd, c->out + c->out_off, c->out_len - c->out_off);
        if (n > 0) {
            c->out_off += (size_t)n;
        } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return true;
        } else if (n < 0 && errno == EINTR) {
            continue;
        } else {
            return false;
        }
    }
    c->out_off = c->out_len = 0;
    return true;
}

static void accept_clients(int lfd, client *clients)
{
    for (;;) {
        int fd = accept4(lfd, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (fd < 0)
            return;
        struct ucred cred;
        socklen_t clen = sizeof(cred);
        if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &clen) < 0) {
            close(fd);
            continue;
        }
        client *slot = NULL;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (clients[i].fd < 0) {
                slot = &clients[i];
                break;
            }
        }
        if (!slot || set_nonblocking(fd) < 0) {
            close(fd);
            continue;
        }
        memset(slot, 0, sizeof(*slot));
        slot->fd = fd;
        slot->uid = cred.uid;
    }
}

int cfs_serve(cfs_ctx *ctx, const char *socket_path, unsigned mode, volatile sig_atomic_t *stop)
{
    int lfd = listen_on(socket_path, mode);
    if (lfd < 0)
        return -1;
    client clients[MAX_CLIENTS];
    for (int i = 0; i < MAX_CLIENTS; i++) {
        memset(&clients[i], 0, sizeof(clients[i]));
        clients[i].fd = -1;
    }
    struct pollfd fds[MAX_CLIENTS + 1];
    int idx[MAX_CLIENTS + 1];

    while (!*stop) {
        int n = 0;
        fds[n] = (struct pollfd){.fd = lfd, .events = POLLIN};
        idx[n++] = -1;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (clients[i].fd < 0)
                continue;
            short ev = POLLIN;
            if (clients[i].out_len > clients[i].out_off)
                ev |= POLLOUT;
            fds[n] = (struct pollfd){.fd = clients[i].fd, .events = ev};
            idx[n++] = i;
        }
        /* A timeout lets the stop flag be seen even if a signal lands just before poll(). */
        int rc = poll(fds, (nfds_t)n, 1000);
        if (rc < 0) {
            if (errno == EINTR)
                continue;
            perror("hyperlite-cfs: poll");
            break;
        }
        for (int k = 0; k < n; k++) {
            if (!fds[k].revents)
                continue;
            if (idx[k] < 0) {
                accept_clients(lfd, clients);
                continue;
            }
            client *c = &clients[idx[k]];
            bool ok = true;
            if (fds[k].revents & POLLIN)
                ok = read_client(ctx, c);
            if (ok && (fds[k].revents & (POLLERR | POLLNVAL)))
                ok = false;
            if (ok && c->out_len > c->out_off)
                ok = write_client(c);
            if (ok && (fds[k].revents & POLLHUP) && !(fds[k].revents & POLLIN))
                ok = false;
            if (!ok)
                client_close(c);
        }
    }
    for (int i = 0; i < MAX_CLIENTS; i++)
        if (clients[i].fd >= 0)
            client_close(&clients[i]);
    close(lfd);
    unlink(socket_path);
    return 0;
}
