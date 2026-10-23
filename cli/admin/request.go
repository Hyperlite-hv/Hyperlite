package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/Hyperlite-hv/Hyperlite/cli/hyperlite"
)

type request struct {
	method string
	path   string // with its query string, without the API prefix
	body   any    // nil, or what is sent as JSON
}

// buildRequest turns `<path> [items]` into a call: key==value goes to the query, key:=json and (but for get and
// delete) key=value to the body.
func buildRequest(method string, o options, stdin io.Reader) (*request, error) {
	if len(o.rest) == 0 {
		return nil, usageError("%s needs a path, e.g. /vms", strings.ToLower(method))
	}
	path := o.rest[0]
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	// The prefix is the client's to add: a path copied from the documentation keeps working.
	path = strings.TrimPrefix(path, hyperlite.APIPrefix)
	base, rawQuery, _ := strings.Cut(path, "?")
	query, err := url.ParseQuery(rawQuery)
	if err != nil {
		return nil, usageError("the path's query string cannot be read: %v", err)
	}
	if o.node != "" {
		query.Set("node", o.node)
	}
	queryOnly := method == "GET" || method == "DELETE"
	fields := map[string]any{}
	for _, raw := range o.rest[1:] {
		it, err := parseItem(raw)
		if err != nil {
			return nil, err
		}
		switch {
		case it.kind == itemQuery || (it.kind == itemText && queryOnly):
			query.Add(it.key, it.text)
		case it.kind == itemJSON && queryOnly:
			return nil, usageError("%s takes no body: give %s as %s==%s", strings.ToLower(method), it.key, it.key, it.text)
		case it.kind == itemJSON:
			fields[it.key] = it.json
		default:
			fields[it.key] = it.text
		}
	}
	req := &request{method: method, path: base}
	if len(query) > 0 {
		req.path += "?" + query.Encode()
	}
	switch {
	case o.dataSet && len(fields) > 0:
		return nil, usageError("give the body either with --data or with key=value items, not both")
	case o.dataSet:
		if queryOnly {
			return nil, usageError("%s takes no body", strings.ToLower(method))
		}
		raw, err := readData(o.data, stdin)
		if err != nil {
			return nil, err
		}
		req.body = raw
	case len(fields) > 0:
		req.body = fields
	case method == "POST" || method == "PUT" || method == "PATCH":
		req.body = map[string]any{} // FastAPI wants a body where the route declares one, even empty
	}
	return req, nil
}

const (
	itemText  = iota // key=value
	itemQuery        // key==value
	itemJSON         // key:=json
)

type item struct {
	kind int
	key  string
	text string // the value as written
	json any    // the value of key:=json
}

// parseItem reads one item as httpie does: the first "=" ends the key, and the characters around it say which kind it
// is, so a value may hold "=", "==" or ":=" (password=a==b is a text field, not a query parameter).
func parseItem(raw string) (item, error) {
	i := strings.IndexByte(raw, '=')
	if i <= 0 {
		return item{}, usageError("%q is not an item: use key=value, key==value or key:=json", raw)
	}
	switch {
	case raw[i-1] == ':':
		if i == 1 {
			return item{}, usageError("%q has no key before :=", raw)
		}
		it := item{kind: itemJSON, key: raw[:i-1], text: raw[i+1:]}
		if err := json.Unmarshal([]byte(it.text), &it.json); err != nil {
			return item{}, usageError("%s: the value after := must be JSON: %v", it.key, err)
		}
		return it, nil
	case i+1 < len(raw) && raw[i+1] == '=':
		return item{kind: itemQuery, key: raw[:i], text: raw[i+2:]}, nil
	}
	return item{kind: itemText, key: raw[:i], text: raw[i+1:]}, nil
}

// readData is the body of --data: inline JSON, @FILE or - for standard input, checked to be JSON before it is sent.
func readData(spec string, stdin io.Reader) (json.RawMessage, error) {
	var data []byte
	var err error
	switch {
	case spec == "-":
		data, err = io.ReadAll(io.LimitReader(stdin, 16<<20))
	case strings.HasPrefix(spec, "@"):
		data, err = os.ReadFile(spec[1:])
	default:
		data = []byte(spec)
	}
	if err != nil {
		return nil, usageError("--data: %v", err)
	}
	data = bytes.TrimSpace(data)
	if !json.Valid(data) {
		return nil, usageError("--data is not valid JSON")
	}
	return json.RawMessage(data), nil
}

// session is the client of one run, with the credential it renews.
type session struct {
	client *hyperlite.Client
	cred   credential
}

func newSession(o options) (*session, error) {
	cred, err := pickCredential(o)
	if err != nil {
		return nil, err
	}
	c := &hyperlite.Client{
		URL:         strings.TrimRight(o.server, "/"),
		Fingerprint: pinnedFingerprint(o.cert),
		UserAgent:   "hyperlite-admin/" + version,
	}
	return &session{client: c, cred: cred}, nil
}

// call sends one request with a fresh credential.
func (s *session) call(ctx context.Context, method, path string, in, out any) error {
	token, err := s.cred.token(ctx)
	if err != nil {
		return err
	}
	s.client.Token = token
	return s.client.Call(ctx, method, path, in, out)
}

// taskAnswer is the answer of a long operation: 202 with the task (app/core/tasks.py, answer_later), or the answer of
// a member that runs one (`?node=`).
type taskAnswer struct {
	TaskID string `json:"task_id"`
	Status string `json:"status"`
}

func (s *session) do(ctx context.Context, req *request, o options, stdout, stderr io.Writer) error {
	var raw json.RawMessage
	if err := s.call(ctx, req.method, req.path, req.body, &raw); err != nil {
		return err
	}
	var t taskAnswer
	if !o.noWait && json.Unmarshal(raw, &t) == nil && t.TaskID != "" && (t.Status == "running" || t.Status == "pending") {
		return s.wait(ctx, t.TaskID, o.node, o, stdout, stderr)
	}
	return printAnswer(stdout, raw, o.json)
}

// wait follows a task until it ends, printing its log as it comes (to stderr with --json, so stdout stays the task).
// A task is never cancelled here: the run that stops waiting leaves it to go on, as closing the dashboard does.
func (s *session) wait(ctx context.Context, id, node string, o options, stdout, stderr io.Writer) error {
	logOut := stdout
	if o.json {
		logOut = stderr
	}
	suffix := ""
	if node != "" {
		suffix = "?on=" + url.QueryEscape(node)
	}
	taskPath := "/tasks/" + url.PathEscape(id)
	printed := 0
	start := time.Now()
	for {
		var task hyperlite.Task
		var raw json.RawMessage
		err := s.call(ctx, "GET", taskPath+suffix, nil, &raw)
		if err == nil {
			err = json.Unmarshal(raw, &task)
		}
		if err != nil {
			if ctx.Err() != nil {
				return stillRunning(id, node)
			}
			return err
		}
		printed = s.printLog(ctx, taskPath+"/log"+suffix, printed, logOut)
		if task.Done() {
			if o.json {
				fmt.Fprintln(stdout, string(indent(raw)))
			}
			if task.Status == "failed" {
				msg := fmt.Sprintf("task %s (%s of %s) failed", task.ID, task.Type, task.Target)
				if task.Error != "" {
					msg += ": " + task.Error
				}
				return &exitError{exitTask, errors.New(msg)}
			}
			if !o.json {
				fmt.Fprintf(stdout, "task %s (%s of %s) succeeded\n", task.ID, task.Type, task.Target)
			}
			return nil
		}
		interval := time.Second
		if time.Since(start) > time.Minute {
			interval = 5 * time.Second
		}
		select {
		case <-ctx.Done():
			return stillRunning(id, node)
		case <-time.After(interval):
		}
	}
}

func stillRunning(id, node string) error {
	again := "hyperlite-admin wait " + id
	if node != "" {
		again += " --node " + node
	}
	return &exitError{exitTimeout, fmt.Errorf("task %s is still running; follow it with: %s", id, again)}
}

// printLog prints the lines of the task's log after the first `printed`, and returns how many are printed now. A log
// that cannot be read is skipped: the task's own state is what decides the outcome.
func (s *session) printLog(ctx context.Context, path string, printed int, w io.Writer) int {
	var log struct {
		Items []struct {
			At      string `json:"at"`
			Message string `json:"message"`
		} `json:"items"`
	}
	if err := s.call(ctx, "GET", path, nil, &log); err != nil {
		return printed
	}
	for _, l := range log.Items[min(printed, len(log.Items)):] {
		fmt.Fprintln(w, l.At, l.Message)
	}
	return max(printed, len(log.Items))
}
