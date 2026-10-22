package hyperlite

import (
	"context"
	"fmt"
	"net/url"
	"strings"
	"time"
)

// Task is a long operation of the server (GET /tasks/{id}).
type Task struct {
	ID          string `json:"id"`
	Type        string `json:"type"`
	Target      string `json:"target"`
	Node        string `json:"node"`
	Status      string `json:"status"` // pending, running, succeeded, failed
	Progress    int    `json:"progress"`
	Error       string `json:"error"`
	Cancellable bool   `json:"cancellable"`
}

// Done tells whether the task has ended, well or not.
func (t *Task) Done() bool { return t.Status == "succeeded" || t.Status == "failed" }

// TaskFailedError is a task that ended in failure: its error and the last lines of its own log, which say where.
type TaskFailedError struct {
	Task *Task
	Log  []string
}

func (e *TaskFailedError) Error() string {
	msg := fmt.Sprintf("task %s (%s of %s) failed", e.Task.ID, e.Task.Type, e.Task.Target)
	if e.Task.Error != "" {
		msg += ": " + e.Task.Error
	}
	if len(e.Log) > 0 {
		msg += "\n" + strings.Join(e.Log, "\n")
	}
	return msg
}

// WaitOptions tunes WaitTask. The zero value polls every second, then every 5 s after a minute.
type WaitOptions struct {
	Node     string        // the member that holds the task in a cluster; empty for the server asked
	Interval time.Duration // first polling interval
	Slower   time.Duration // interval after the first minute
	LogLines int           // lines of the task's log kept in a TaskFailedError (default 20)
}

func taskPath(id, node, suffix string) string {
	p := "/tasks/" + url.PathEscape(id) + suffix
	if node != "" {
		p += "?on=" + url.QueryEscape(node)
	}
	return p
}

// GetTask reads a task once.
func (c *Client) GetTask(ctx context.Context, id, node string) (*Task, error) {
	var t Task
	if err := c.Call(ctx, "GET", taskPath(id, node, ""), nil, &t); err != nil {
		return nil, err
	}
	return &t, nil
}

// WaitTask follows a task until it ends: nil when it succeeded, a *TaskFailedError when it failed. When ctx ends
// first (a timeout), the task is asked to stop if it can, and the error says whether it was.
func (c *Client) WaitTask(ctx context.Context, id string, opts WaitOptions) (*Task, error) {
	interval, slower := opts.Interval, opts.Slower
	if interval == 0 {
		interval = time.Second
	}
	if slower == 0 {
		slower = 5 * time.Second
	}
	start := time.Now()
	var last *Task // what is known of the task when the wait ends in the middle of a read
	for {
		t, err := c.GetTask(ctx, id, opts.Node)
		if err != nil {
			if ctx.Err() != nil {
				return last, c.stopOnTimeout(id, opts.Node, last, ctx.Err())
			}
			return last, err
		}
		last = t
		if t.Done() {
			if t.Status == "succeeded" {
				return t, nil
			}
			return t, &TaskFailedError{Task: t, Log: c.lastLogLines(ctx, id, opts)}
		}
		wait := interval
		if time.Since(start) > time.Minute {
			wait = slower
		}
		select {
		case <-ctx.Done():
			return t, c.stopOnTimeout(id, opts.Node, t, ctx.Err())
		case <-time.After(wait):
		}
	}
}

// stopOnTimeout asks a task that outlived its wait to stop, with a context of its own (the caller's has ended).
func (c *Client) stopOnTimeout(id, node string, t *Task, cause error) error {
	if t == nil || !t.Cancellable {
		return fmt.Errorf("task %s still running after the wait: %w", id, cause)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := c.Call(ctx, "POST", taskPath(id, node, "/cancel"), nil, nil); err != nil {
		return fmt.Errorf("task %s still running after the wait, and could not be stopped (%v): %w", id, err, cause)
	}
	return fmt.Errorf("task %s did not end in time and was asked to stop: %w", id, cause)
}

func (c *Client) lastLogLines(ctx context.Context, id string, opts WaitOptions) []string {
	n := opts.LogLines
	if n == 0 {
		n = 20
	}
	var log struct {
		Items []struct {
			At      string `json:"at"`
			Message string `json:"message"`
		} `json:"items"`
	}
	if err := c.Call(ctx, "GET", taskPath(id, opts.Node, "/log"), nil, &log); err != nil {
		// The failure itself is what matters; a log that cannot be read is said, not hidden.
		return []string{"(the task's log could not be read: " + err.Error() + ")"}
	}
	items := log.Items
	if len(items) > n {
		items = items[len(items)-n:]
	}
	lines := make([]string, 0, len(items))
	for _, l := range items {
		lines = append(lines, l.At+" "+l.Message)
	}
	return lines
}
