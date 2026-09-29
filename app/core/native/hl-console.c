/*
 * Starts an application container's process with its standard output and error sent to a log file, so that
 * what a process prints before it stops can be read from the dashboard (libvirt's LXC driver only offers a
 * console pty, which loses everything printed before a reader attaches: the case of a program that fails at
 * once). The process then replaces this launcher (exec), so it is still the container's PID 1.
 *
 * Usage: hl-console LOG PROGRAM [ARG...]    (PROGRAM is looked up in PATH like a shell would)
 * Built statically by Hyperlite (app/core/container_console.py): container images often have no libc at all.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s LOG PROGRAM [ARG...]\n", argv[0]);
        return 127;
    }
    int fd = open(argv[1], O_WRONLY | O_CREAT | O_APPEND, 0640);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        if (fd > STDERR_FILENO) close(fd);
    }
    dprintf(STDERR_FILENO, "--- hyperlite: starting %s\n", argv[2]);
    execvp(argv[2], argv + 2);
    dprintf(STDERR_FILENO, "--- hyperlite: cannot start %s: %s\n", argv[2], strerror(errno));
    return 127;
}
