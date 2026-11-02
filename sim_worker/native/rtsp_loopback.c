/* Restrict only the configured native RTSP wildcard bind. Linux/glibc only. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

static int (*next_bind)(int, const struct sockaddr *, socklen_t);
static unsigned long guarded_port;

__attribute__((constructor)) static void initialize(void) {
    void *symbol = dlsym(RTLD_NEXT, "bind");
    _Static_assert(sizeof(next_bind) == sizeof(symbol), "Unsupported function pointer ABI");
    memcpy(&next_bind, &symbol, sizeof(next_bind));
    const char *raw = getenv("REPLAY_RTSP_LOOPBACK_PORT");
    if (raw && *raw) {
        char *end;
        unsigned long value = strtoul(raw, &end, 10);
        if (!*end && value >= 1024 && value <= 65535) guarded_port = value;
    }
}

int bind(int fd, __CONST_SOCKADDR_ARG address_arg, socklen_t length) {
    const struct sockaddr *address = address_arg.__sockaddr__;
    if (!next_bind) { errno = ENOSYS; return -1; }
    if (guarded_port && address && address->sa_family == AF_INET && length >= sizeof(struct sockaddr_in)) {
        struct sockaddr_in copy;
        memcpy(&copy, address, sizeof(copy));
        if (ntohs(copy.sin_port) == guarded_port && copy.sin_addr.s_addr == htonl(INADDR_ANY)) {
            copy.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            return next_bind(fd, (const struct sockaddr *)&copy, sizeof(copy));
        }
    }
    if (guarded_port && address && address->sa_family == AF_INET6 && length >= sizeof(struct sockaddr_in6)) {
        struct sockaddr_in6 copy;
        memcpy(&copy, address, sizeof(copy));
        if (ntohs(copy.sin6_port) == guarded_port && IN6_IS_ADDR_UNSPECIFIED(&copy.sin6_addr)) {
            copy.sin6_addr = in6addr_loopback;
            return next_bind(fd, (const struct sockaddr *)&copy, sizeof(copy));
        }
    }
    return next_bind(fd, address, length);
}
