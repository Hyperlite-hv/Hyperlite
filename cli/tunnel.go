package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
)

// Same rule as the server side: VM names never contain anything a shell or a URL
// could interpret.
var vmNameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$`)

func checkVM(name string) error {
	if !vmNameRe.MatchString(name) {
		return fmt.Errorf("invalid VM name %q", name)
	}
	return nil
}

// dialTunnel asks for a single-use ticket, then opens the WebSocket relay to
// port of vm. The returned connection carries the raw bytes of the guest service.
func dialTunnel(ctx context.Context, s *server, vm string, port int) (net.Conn, error) {
	if err := checkVM(vm); err != nil {
		return nil, err
	}
	var ticket struct {
		Ticket string `json:"ticket"`
	}
	if err := call(s, "POST", "/vms/"+url.PathEscape(vm)+"/tunnel-ticket", map[string]int{"port": port}, &ticket); err != nil {
		return nil, err
	}
	wsURL := strings.Replace(s.URL, "http", "ws", 1) + "/vms/" + url.PathEscape(vm) + "/tunnel?ticket=" + url.QueryEscape(ticket.Ticket)
	dialCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	c, _, err := websocket.Dial(dialCtx, wsURL, &websocket.DialOptions{
		HTTPClient: newHTTPClient(s),
		HTTPHeader: http.Header{"User-Agent": {"hyperlite-cli/" + version}},
	})
	if err != nil {
		return nil, fmt.Errorf("tunnel to %s:%d: %w", vm, port, err)
	}
	c.SetReadLimit(1 << 20)
	return websocket.NetConn(ctx, c, websocket.MessageBinary), nil
}

// closeReason turns the close frame of the server into a readable message.
func closeReason(err error) error {
	var ce websocket.CloseError
	if errors.As(err, &ce) {
		switch ce.Code {
		case websocket.StatusNormalClosure, websocket.StatusGoingAway:
			return nil
		}
		if ce.Reason != "" {
			return fmt.Errorf("tunnel closed by the server: %s", ce.Reason)
		}
		return fmt.Errorf("tunnel closed by the server (code %d)", ce.Code)
	}
	if errors.Is(err, io.EOF) || errors.Is(err, net.ErrClosed) || errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}

// pipe copies both ways until one side ends.
func pipe(a io.ReadWriteCloser, b io.ReadWriteCloser) error {
	var once sync.Once
	var firstErr error
	done := make(chan struct{})
	cp := func(dst io.Writer, src io.Reader) {
		_, err := io.Copy(dst, src)
		once.Do(func() { firstErr = err; close(done) })
	}
	go cp(a, b)
	go cp(b, a)
	<-done
	a.Close()
	b.Close()
	return firstErr
}

type stdio struct{}

func (stdio) Read(p []byte) (int, error)  { return os.Stdin.Read(p) }
func (stdio) Write(p []byte) (int, error) { return os.Stdout.Write(p) }
func (stdio) Close() error                { return os.Stdout.Close() }

func parsePort(raw string) (int, error) {
	p, err := strconv.Atoi(raw)
	if err != nil || p < 1 || p > 65535 {
		return 0, fmt.Errorf("invalid port %q", raw)
	}
	return p, nil
}

func cmdTunnel(flag, vm, rawPort, listen string, useStdio bool) error {
	port, err := parsePort(rawPort)
	if err != nil {
		return err
	}
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	s, err := cfg.pick(flag)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if useStdio {
		// ProxyCommand mode: OpenSSH talks to the VM through stdin/stdout.
		conn, err := dialTunnel(ctx, s, vm, port)
		if err != nil {
			return err
		}
		return closeReason(pipe(conn, stdio{}))
	}
	if listen == "" {
		listen = "127.0.0.1:0"
	}
	ln, err := net.Listen("tcp", listen)
	if err != nil {
		return err
	}
	fmt.Printf("Forwarding %s → %s:%d (Ctrl+C to stop)\n", ln.Addr(), vm, port)
	return serveForward(ctx, ln, s, vm, port, nil)
}

// serveForward accepts local connections and opens one tunnel per connection.
// onFirst, when set, is called once the listener is ready (to start a client).
func serveForward(ctx context.Context, ln net.Listener, s *server, vm string, port int, until <-chan struct{}) error {
	go func() {
		select {
		case <-ctx.Done():
		case <-until:
		}
		ln.Close()
	}()
	var wg sync.WaitGroup
	for {
		local, err := ln.Accept()
		if err != nil {
			wg.Wait()
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			remote, err := dialTunnel(ctx, s, vm, port)
			if err != nil {
				fmt.Fprintln(os.Stderr, "hyperlite:", err)
				local.Close()
				return
			}
			if err := closeReason(pipe(remote, local)); err != nil {
				fmt.Fprintln(os.Stderr, "hyperlite:", err)
			}
		}()
	}
}
