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
#include <stdbool.h>
#include <time.h>
#include <unistd.h>

#define MAX_CLIENTS 128
#define MAX_SOURCES 4
#define MAX_PENDING_OUT (8u * CFS_FRAME_MAX) /* a client that stops reading is dropped */

typedef struct {
    int fd;
    uid_t uid;
    uint32_t gen;  /* with the slot index, the token that names this client to the node */
    bool waiting;  /* a change of this client is in the cluster: its next requests wait */
    bool dead;     /* to be closed by the loop (it stopped reading its answers) */
    uint8_t *in;
    size_t in_len;
    size_t in_cap;
    uint8_t *out;
    size_t out_len;
    size_t out_off;
    size_t out_cap;
} client;

typedef struct {
    client clients[MAX_CLIENTS];
    cfs_node *node;
    uint32_t next_gen;
} server;

static uint64_t token_of(const server *srv, const client *c)
{
    return (uint64_t)c->gen << 32 | (uint64_t)(c - srv->clients);
}

static void client_close(server *srv, client *c)
{
    cfs_node_forget(srv->node, token_of(srv, c));
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

/* The node's answer for a client, maybe much later than its request. */
static void on_reply(void *arg, uint64_t token, const uint8_t *frame, size_t len)
{
    server *srv = arg;
    uint32_t idx = (uint32_t)token;
    if (idx >= MAX_CLIENTS)
        return;
    client *c = &srv->clients[idx];
    if (c->fd < 0 || c->gen != (uint32_t)(token >> 32))
        return; /* that client is gone */
    c->waiting = false;
    if (c->out_len - c->out_off + len > MAX_PENDING_OUT || !reserve(&c->out, &c->out_cap, c->out_len + len)) {
        c->dead = true;
        return;
    }
    memcpy(c->out + c->out_len, frame, len);
    c->out_len += len;
}

/* Hand every complete frame to the node, stopping while one of its changes is in the cluster. Returns false when the
 * client must be dropped. */
static bool process_input(server *srv, client *c)
{
    size_t off = 0;
    while (!c->waiting && !c->dead && c->in_len - off >= 4) {
        const uint8_t *h = c->in + off;
        uint32_t len = (uint32_t)h[0] | (uint32_t)h[1] << 8 | (uint32_t)h[2] << 16 | (uint32_t)h[3] << 24;
        if (len > CFS_FRAME_MAX)
            return false; /* nothing sane follows a corrupt length */
        if (c->in_len - off - 4 < len)
            break;
        c->waiting = true; /* cleared by on_reply, maybe before cfs_node_request returns */
        cfs_node_request(srv->node, h + 4, len, c->uid, token_of(srv, c), (int64_t)time(NULL));
        off += 4 + len;
    }
    if (off) {
        memmove(c->in, c->in + off, c->in_len - off);
        c->in_len -= off;
    }
    return !c->dead;
}

static bool read_client(server *srv, client *c)
{
    for (;;) {
        if (!reserve(&c->in, &c->in_cap, c->in_len + 65536))
            return false;
        ssize_t n = read(c->fd, c->in + c->in_len, c->in_cap - c->in_len);
        if (n > 0) {
            c->in_len += (size_t)n;
            if (!process_input(srv, c))
                return false;
            if (c->waiting)
                return true; /* the rest stays in the kernel until the answer */
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

static void accept_clients(int lfd, server *srv)
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
            if (srv->clients[i].fd < 0) {
                slot = &srv->clients[i];
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
        slot->gen = ++srv->next_gen;
    }
}

int cfs_serve(cfs_node *node, const char *socket_path, unsigned mode, const cfs_source *sources, size_t nsources,
              volatile sig_atomic_t *stop)
{
    if (nsources > MAX_SOURCES)
        return -1;
    int lfd = listen_on(socket_path, mode);
    if (lfd < 0)
        return -1;
    static server srv; /* large: kept off the stack */
    memset(&srv, 0, sizeof(srv));
    srv.node = node;
    for (int i = 0; i < MAX_CLIENTS; i++)
        srv.clients[i].fd = -1;
    node->reply = on_reply;
    node->reply_arg = &srv;
    struct pollfd fds[MAX_CLIENTS + MAX_SOURCES + 1];
    int idx[MAX_CLIENTS + MAX_SOURCES + 1];
    int result = 0;

    while (!*stop) {
        int n = 0;
        fds[n] = (struct pollfd){.fd = lfd, .events = POLLIN};
        idx[n++] = -1;
        for (size_t i = 0; i < nsources; i++) {
            fds[n] = (struct pollfd){.fd = sources[i].fd, .events = POLLIN};
            idx[n++] = -2 - (int)i;
        }
        for (int i = 0; i < MAX_CLIENTS; i++) {
            client *c = &srv.clients[i];
            if (c->fd < 0)
                continue;
            short ev = c->waiting ? 0 : POLLIN; /* a waiting client's next requests stay in the kernel */
            if (c->out_len > c->out_off)
                ev |= POLLOUT;
            fds[n] = (struct pollfd){.fd = c->fd, .events = ev};
            idx[n++] = i;
        }
        /* A timeout lets the stop flag be seen even if a signal lands just before poll(); a short one while the
         * transport asked to retry a message. */
        int rc = poll(fds, (nfds_t)n, node->nqueue ? 10 : 1000);
        if (rc < 0) {
            if (errno == EINTR)
                continue;
            perror("hyperlite-cfs: poll");
            result = -1;
            break;
        }
        for (int k = 0; k < n && result == 0; k++) {
            if (!fds[k].revents)
                continue;
            if (idx[k] == -1) {
                accept_clients(lfd, &srv);
                continue;
            }
            if (idx[k] <= -2) {
                const cfs_source *src = &sources[-2 - idx[k]];
                if (src->ready(src->arg) < 0)
                    result = -1;
                continue;
            }
            client *c = &srv.clients[idx[k]];
            if (c->fd < 0)
                continue;
            bool ok = true;
            if (fds[k].revents & POLLIN)
                ok = read_client(&srv, c);
            if (ok && (fds[k].revents & (POLLERR | POLLNVAL)))
                ok = false;
            if (ok && (fds[k].revents & POLLHUP) && !(fds[k].revents & POLLIN))
                ok = false;
            if (!ok)
                c->dead = true;
        }
        if (node->nqueue)
            cfs_node_flush(node);
        /* Answers may have arrived for any client: send them, and go on with requests that were waiting. */
        for (int i = 0; i < MAX_CLIENTS; i++) {
            client *c = &srv.clients[i];
            if (c->fd < 0)
                continue;
            if (!c->dead && !c->waiting && c->in_len >= 4 && !process_input(&srv, c))
                c->dead = true;
            if (!c->dead && c->out_len > c->out_off && !write_client(c))
                c->dead = true;
            if (c->dead)
                client_close(&srv, c);
        }
    }
    for (int i = 0; i < MAX_CLIENTS; i++)
        if (srv.clients[i].fd >= 0)
            client_close(&srv, &srv.clients[i]);
    node->reply = NULL;
    close(lfd);
    unlink(socket_path);
    return result;
}
