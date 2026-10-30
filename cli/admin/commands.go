package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"sort"
	"strings"
)

// short is a command for daily work: one route, its arguments in order, nothing the API does not do itself. What it
// does not name is still given as items (key=value...) or reached through the generic layer.
type short struct {
	method string
	path   string   // {placeholders} filled by the arguments of the same name, in the order of args
	args   []string // "{x}": a placeholder of path; "body:x": a field of the body; "{x}?": may be left out, for self
	self   string   // the route when the optional argument is left out: this node's own, which /nodes does not list
	ask    string   // asked before acting, unless --yes: what the action destroys or disturbs
	query  string   // sent once asked: the route's own confirmation (confirm=true)
	onNode bool     // the routes of a backup name the member with ?on=, not ?node=
	help   string
}

// groups are the commands of hyperlite-admin <group> <action...>, from the routes of docs/api/openapi-v1.json.
var groups = map[string]map[string]short{
	"vm": {
		"list":             {method: "GET", path: "/vms", help: "The VMs of this node, or of every member with --cluster"},
		"show":             {method: "GET", path: "/vms/{vm}", args: []string{"{vm}"}, help: "One VM"},
		"start":            {method: "POST", path: "/vms/{vm}/start", args: []string{"{vm}"}, help: "Start a VM"},
		"stop":             {method: "POST", path: "/vms/{vm}/stop", args: []string{"{vm}"}, help: "Shut a VM down (--force: power it off)"},
		"restart":          {method: "POST", path: "/vms/{vm}/restart", args: []string{"{vm}"}, help: "Restart a VM (--force: reset it)"},
		"migrate":          {method: "POST", path: "/vms/{vm}/migrate", args: []string{"{vm}", "body:target_node"}, help: "Move a VM to another member, live when it runs"},
		"clone":            {method: "POST", path: "/vms/{vm}/clone", args: []string{"{vm}", "body:new_name"}, help: "Copy a stopped VM"},
		"delete":           {method: "DELETE", path: "/vms/{vm}", args: []string{"{vm}"}, ask: "delete the VM %s and its disks", query: "confirm", help: "Delete a VM"},
		"backup":           {method: "POST", path: "/vms/{vm}/backups", args: []string{"{vm}"}, help: "Back a VM up now"},
		"backups":          {method: "GET", path: "/vms/{vm}/backups", args: []string{"{vm}"}, help: "A VM's backups"},
		"snapshot list":    {method: "GET", path: "/vms/{vm}/snapshots", args: []string{"{vm}"}, help: "A VM's snapshots"},
		"snapshot create":  {method: "POST", path: "/vms/{vm}/snapshots", args: []string{"{vm}", "body:name"}, help: "Take a snapshot"},
		"snapshot restore": {method: "POST", path: "/vms/{vm}/snapshots/{snapshot}/restore", args: []string{"{vm}", "{snapshot}"}, ask: "return the VM %s to the snapshot %s, losing what changed since", query: "confirm", help: "Return a VM to a snapshot"},
		"snapshot delete":  {method: "DELETE", path: "/vms/{vm}/snapshots/{snapshot}", args: []string{"{vm}", "{snapshot}"}, ask: "delete the snapshot %[2]s of the VM %[1]s", help: "Delete a snapshot"},
	},
	"ct": {
		"list":             {method: "GET", path: "/containers", help: "The containers of this node"},
		"show":             {method: "GET", path: "/containers/{ct}", args: []string{"{ct}"}, help: "One container"},
		"start":            {method: "POST", path: "/containers/{ct}/start", args: []string{"{ct}"}, help: "Start a container"},
		"stop":             {method: "POST", path: "/containers/{ct}/stop", args: []string{"{ct}"}, help: "Stop a container (--force: kill it)"},
		"clone":            {method: "POST", path: "/containers/{ct}/clone", args: []string{"{ct}", "body:new_name"}, help: "Copy a container"},
		"delete":           {method: "DELETE", path: "/containers/{ct}", args: []string{"{ct}"}, ask: "delete the container %s", query: "confirm", help: "Delete a container"},
		"logs":             {method: "GET", path: "/containers/{ct}/logs", args: []string{"{ct}"}, help: "A container's last log lines (lines==N)"},
		"snapshot list":    {method: "GET", path: "/containers/{ct}/snapshots", args: []string{"{ct}"}, help: "A container's snapshots"},
		"snapshot create":  {method: "POST", path: "/containers/{ct}/snapshots", args: []string{"{ct}", "body:name"}, help: "Take a snapshot"},
		"snapshot restore": {method: "POST", path: "/containers/{ct}/snapshots/{snapshot}/restore", args: []string{"{ct}", "{snapshot}"}, ask: "return the container %s to the snapshot %s, losing what changed since", help: "Return a container to a snapshot"},
		"snapshot delete":  {method: "DELETE", path: "/containers/{ct}/snapshots/{snapshot}", args: []string{"{ct}", "{snapshot}"}, ask: "delete the snapshot %[2]s of the container %[1]s", help: "Delete a snapshot"},
	},
	"storage": {
		"list":    {method: "GET", path: "/storage", help: "The storage pools"},
		"show":    {method: "GET", path: "/storage/{pool}", args: []string{"{pool}"}, help: "One pool"},
		"volumes": {method: "GET", path: "/storage/{pool}/volumes", args: []string{"{pool}"}, help: "A pool's volumes"},
	},
	"network": {
		"list":  {method: "GET", path: "/networks", help: "The virtual networks"},
		"show":  {method: "GET", path: "/networks/{network}", args: []string{"{network}"}, help: "One network"},
		"start": {method: "POST", path: "/networks/{network}/start", args: []string{"{network}"}, help: "Start a network"},
		"stop":  {method: "POST", path: "/networks/{network}/stop", args: []string{"{network}"}, ask: "stop the network %s: the VMs on it lose their link", query: "confirm", help: "Stop a network"},
	},
	"backup": {
		"list":    {method: "GET", path: "/backups", help: "Every backup of this node, or of every member with --cluster"},
		"restore": {method: "POST", path: "/backups/{backup}/restore", args: []string{"{backup}", "body:mode"}, onNode: true, ask: "restore the backup %s (mode %s)", help: "Restore a backup: mode overwrite (replaces the VM) or new (new_name=...)"},
		"verify":  {method: "POST", path: "/backups/{backup}/verify", args: []string{"{backup}"}, onNode: true, help: "Check a backup against its checksums"},
		"delete":  {method: "DELETE", path: "/backups/{backup}", args: []string{"{backup}"}, onNode: true, ask: "delete the backup %s", query: "confirm", help: "Delete a backup"},
	},
	"task": {
		"list":   {method: "GET", path: "/tasks", help: "The recent tasks (status==running, target==web1...)"},
		"show":   {method: "GET", path: "/tasks/{task}", args: []string{"{task}"}, help: "One task"},
		"log":    {method: "GET", path: "/tasks/{task}/log", args: []string{"{task}"}, help: "A task's log"},
		"cancel": {method: "POST", path: "/tasks/{task}/cancel", args: []string{"{task}"}, help: "Ask a running task to stop (--force: at once)"},
	},
	"cluster": {
		"status":          {method: "GET", path: "/cluster", help: "The cluster, its members and its quorum"},
		"nodes":           {method: "GET", path: "/nodes", help: "The members and their state"},
		"maintenance on":  {method: "POST", path: "/nodes/{node}/maintenance", args: []string{"{node}"}, ask: "put %s in maintenance: its VMs are moved to other members", help: "Empty a member for maintenance (target_node=...)"},
		"maintenance off": {method: "DELETE", path: "/nodes/{node}/maintenance", args: []string{"{node}"}, help: "End a member's maintenance"},
		"update plan":     {method: "GET", path: "/cluster/update/plan", help: "What a rolling update would do"},
		"update":          {method: "POST", path: "/cluster/update", ask: "update every member, one after the other, each emptied first", help: "Rolling update of the cluster"},
		"create":          {method: "POST", path: "/cluster/create", ask: "make this node the first member of a new cluster", help: "Create a cluster on this node (as the Cluster page; items as its form)"},
		"join":            {method: "POST", path: "/cluster/join", ask: "join this node to a cluster", help: "Join a cluster (as the Cluster page; items as its form)"},
	},
	"node": {
		"status":       {method: "GET", path: "/nodes/{node}/summary", args: []string{"{node}?"}, self: "/dashboard", help: "This node's summary, or a member's"},
		"hardware":     {method: "GET", path: "/nodes/{node}/hardware", args: []string{"{node}?"}, self: "/host/capabilities", help: "This node's hardware, or a member's"},
		"update check": {method: "GET", path: "/update/check", help: "The update this node can take"},
		"update":       {method: "POST", path: "/update/apply", ask: "update this node now (its VMs keep running; it may need a reboot)", help: "Update this node, as the dashboard's button"},
	},
}

// actionOf finds the longest action of the group the words begin with ("snapshot create" before "snapshot").
func actionOf(group map[string]short, words []string) (string, []string, bool) {
	for n := min(2, len(words)); n > 0; n-- {
		name := strings.Join(words[:n], " ")
		if _, ok := group[name]; ok {
			return name, words[n:], true
		}
	}
	return "", words, false
}

func printGroup(w io.Writer, name string, group map[string]short) {
	actions := make([]string, 0, len(group))
	for a := range group {
		actions = append(actions, a)
	}
	sort.Strings(actions)
	fmt.Fprintf(w, "hyperlite-admin %s <action>:\n", name)
	for _, a := range actions {
		sh := group[a]
		line := strings.TrimSpace(a + " " + argNames(sh))
		fmt.Fprintf(w, "  %-34s %s\n", line, sh.help)
	}
}

func argNames(sh short) string {
	var names []string
	for _, a := range sh.args {
		spec, optional := strings.CutSuffix(a, "?")
		name := "<" + strings.Trim(strings.TrimPrefix(spec, "body:"), "{}") + ">"
		if optional {
			name = "[" + name + "]"
		}
		names = append(names, name)
	}
	return strings.Join(names, " ")
}

func runShort(ctx context.Context, name string, group map[string]short, o options, stdin io.Reader, stdout, stderr io.Writer) error {
	action, rest, ok := actionOf(group, o.rest)
	if !ok {
		if len(o.rest) == 0 || o.rest[0] == "help" {
			printGroup(stdout, name, group)
			return nil
		}
		return usageError("%s has no action %q: hyperlite-admin %s lists them", name, strings.Join(o.rest, " "), name)
	}
	sh := group[action]
	var values, items []string
	for _, w := range rest {
		if len(values) < len(sh.args) && !strings.Contains(w, "=") {
			values = append(values, w)
		} else {
			items = append(items, w)
		}
	}
	path := sh.path
	body := map[string]any{}
	for i, a := range sh.args {
		spec, optional := strings.CutSuffix(a, "?")
		if i >= len(values) {
			if !optional {
				return usageError("hyperlite-admin %s %s %s", name, action, argNames(sh))
			}
			path = sh.self
			break
		}
		if field, isBody := strings.CutPrefix(spec, "body:"); isBody {
			body[field] = values[i]
		} else {
			path = strings.Replace(path, spec, url.PathEscape(values[i]), 1)
		}
	}
	if sh.ask != "" {
		if err := confirm(fmt.Sprintf(sh.ask, toAny(values)...), o.yes, stdin, stderr); err != nil {
			return err
		}
		if sh.query != "" {
			items = append(items, sh.query+"==true")
		}
	}
	if sh.onNode {
		o.nodeParam = "on"
	}
	o.rest = append([]string{path}, items...)
	req, err := buildRequest(sh.method, o, stdin)
	if err != nil {
		return err
	}
	if len(body) > 0 {
		if req.body, err = mergeBody(req.body, body); err != nil {
			return err
		}
	}
	s, err := newSession(o)
	if err != nil {
		return err
	}
	return s.do(ctx, req, o, stdout, stderr)
}

func toAny(values []string) []any {
	out := make([]any, len(values))
	for i, v := range values {
		out[i] = v
	}
	return out
}

// mergeBody adds the fields the arguments name to the body the items or --data made: an argument wins over an item of
// the same name, since it is the command's own.
func mergeBody(current any, fields map[string]any) (any, error) {
	merged := map[string]any{}
	switch b := current.(type) {
	case map[string]any:
		for k, v := range b {
			merged[k] = v
		}
	case json.RawMessage:
		if err := json.Unmarshal(b, &merged); err != nil {
			return nil, usageError("--data must be a JSON object for this command")
		}
	}
	for k, v := range fields {
		merged[k] = v
	}
	return merged, nil
}

// confirm asks before a destructive action. Without a terminal to answer, it refuses unless --yes: a script states what
// it means rather than piping "y" into a question it never saw.
func confirm(what string, yes bool, stdin io.Reader, stderr io.Writer) error {
	if yes {
		return nil
	}
	f, isFile := stdin.(*os.File)
	if !isFile || !isTerminal(f) {
		return usageError("this would %s: add --yes to do it without a question", what)
	}
	fmt.Fprintf(stderr, "This will %s. Go on? [y/N] ", what)
	line, _ := bufio.NewReader(stdin).ReadString('\n')
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return nil
	}
	return &exitError{exitAPI, fmt.Errorf("nothing done")}
}
