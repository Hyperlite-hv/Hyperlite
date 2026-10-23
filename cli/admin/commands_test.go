package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

const repoSchema = "../../docs/api/openapi-v1.json"

// Every short command names a route and a method of API v1, with the parameters it sends: a route renamed or removed
// fails here, not on a node.
func TestEveryShortCommandIsARouteOfTheAPI(t *testing.T) {
	doc, err := loadSchema(repoSchema)
	if err != nil {
		t.Fatal(err)
	}
	for group, actions := range groups {
		for action, sh := range actions {
			name := group + " " + action
			if sh.self != "" {
				checkRoute(t, doc, name+" (this node)", sh.method, sh.self, short{})
			}
			concrete := sh.path
			for _, a := range sh.args {
				spec := strings.TrimSuffix(a, "?")
				if strings.HasPrefix(spec, "{") {
					concrete = strings.Replace(concrete, spec, "x", 1)
				} else if !strings.HasPrefix(spec, "body:") {
					t.Errorf("%s: argument %q is neither a {placeholder} nor body:field", name, a)
				}
			}
			if strings.Contains(concrete, "{") {
				t.Errorf("%s: %s has a placeholder no argument fills", name, sh.path)
				continue
			}
			checkRoute(t, doc, name, sh.method, concrete, sh)
		}
	}
}

func checkRoute(t *testing.T, doc *openAPI, name, method, concrete string, sh short) {
	t.Helper()
	var op *operation
	for p, ops := range doc.Paths {
		if o, ok := ops[strings.ToLower(method)]; ok && matches(strings.TrimPrefix(p, "/api/v1"), concrete) {
			op = &o
			break
		}
	}
	if op == nil {
		t.Errorf("%s: no %s %s in API v1", name, method, concrete)
		return
	}
	query := map[string]bool{}
	for _, p := range op.Parameters {
		if p.In == "query" {
			query[p.Name] = true
		}
	}
	if sh.query != "" && !query[sh.query] {
		t.Errorf("%s: the route takes no %s", name, sh.query)
	}
	if sh.onNode && !query["on"] {
		t.Errorf("%s: the route takes no on", name)
	}
	if sh.query != "" && sh.ask == "" {
		t.Errorf("%s: confirms with %s without asking first", name, sh.query)
	}
}

type seen struct {
	method, uri string
	body        map[string]any
}

// recorder is an API that answers {} to everything and keeps what it was asked.
func recorder(t *testing.T) (*fakeAPI, *[]seen) {
	f := newFakeAPI(t, "succeeded", 0)
	var mu sync.Mutex
	var calls []seen
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/", func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(data, &body)
		mu.Lock()
		calls = append(calls, seen{r.Method, strings.TrimPrefix(r.URL.RequestURI(), "/api/v1"), body})
		mu.Unlock()
		w.Write([]byte(`{}`))
	})
	f.srv.Config.Handler = mux
	return f, &calls
}

func TestShortCommandsCallTheirRoute(t *testing.T) {
	cases := []struct {
		args   []string
		method string
		uri    string
		body   string
	}{
		{[]string{"vm", "list", "--cluster"}, "GET", "/vms?cluster=true", "null"},
		{[]string{"vm", "stop", "web1", "--force"}, "POST", "/vms/web1/stop?force=true", "{}"},
		{[]string{"vm", "show", "a b"}, "GET", "/vms/a%20b", "null"},
		{[]string{"vm", "migrate", "web1", "n2", "skip_checks:=true"}, "POST", "/vms/web1/migrate", `{"skip_checks":true,"target_node":"n2"}`},
		{[]string{"vm", "snapshot", "create", "web1", "before-upgrade", "description=x=y"}, "POST", "/vms/web1/snapshots", `{"description":"x=y","name":"before-upgrade"}`},
		{[]string{"vm", "delete", "web1", "--yes", "--node", "n2"}, "DELETE", "/vms/web1?confirm=true&node=n2", "null"},
		{[]string{"vm", "snapshot", "restore", "web1", "s1", "-y"}, "POST", "/vms/web1/snapshots/s1/restore?confirm=true", "{}"},
		{[]string{"backup", "restore", "7", "new", "new_name=web2", "--node", "n2", "--yes"}, "POST", "/backups/7/restore?on=n2", `{"mode":"new","new_name":"web2"}`},
		{[]string{"node", "status"}, "GET", "/dashboard", "null"},
		{[]string{"node", "hardware"}, "GET", "/host/capabilities", "null"},
		{[]string{"node", "status", "n2"}, "GET", "/nodes/n2/summary", "null"},
		{[]string{"node", "update", "check"}, "GET", "/update/check", "null"},
		{[]string{"task", "list", "status==running"}, "GET", "/tasks?status=running", "null"},
		{[]string{"cluster", "maintenance", "on", "n2", "target_node=n3", "--yes"}, "POST", "/nodes/n2/maintenance", `{"target_node":"n3"}`},
	}
	for _, c := range cases {
		f, calls := recorder(t)
		code, _, errOut := f.run(c.args...)
		if code != 0 || len(*calls) != 1 {
			t.Errorf("%v: exit %d, %d calls: %s", c.args, code, len(*calls), errOut)
			continue
		}
		got := (*calls)[0]
		body, _ := json.Marshal(got.body)
		if got.method != c.method || got.uri != c.uri || string(body) != c.body {
			t.Errorf("%v: %s %s %s, want %s %s %s", c.args, got.method, got.uri, body, c.method, c.uri, c.body)
		}
	}
}

func TestADestructiveCommandAsksOrTakesYes(t *testing.T) {
	f, calls := recorder(t)
	code, _, errOut := f.run("vm", "delete", "web1")
	if code != exitUsage || !strings.Contains(errOut, "add --yes") || len(*calls) != 0 {
		t.Fatalf("exit %d, %d calls: %s", code, len(*calls), errOut)
	}
}

func TestAShortCommandSaysWhatItNeeds(t *testing.T) {
	f, calls := recorder(t)
	for _, args := range [][]string{{"vm", "migrate", "web1"}, {"vm", "frobnicate"}, {"vm", "snapshot"}} {
		if code, _, errOut := f.run(args...); code != exitUsage {
			t.Errorf("%v: exit %d: %s", args, code, errOut)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("a wrong command reached the API: %v", *calls)
	}
	var out bytes.Buffer
	if code := run([]string{"vm"}, nil, &out, io.Discard); code != 0 || !strings.Contains(out.String(), "migrate <vm> <target_node>") {
		t.Errorf("the group's help: %d\n%s", code, out.String())
	}
}

func TestAPIPrintsWhatARouteTakes(t *testing.T) {
	var out, errOut bytes.Buffer
	code := run([]string{"api", "/api/v1/vms/web1/migrate", "--schema", repoSchema}, nil, &out, &errOut)
	if code != 0 || !strings.HasPrefix(out.String(), "POST /vms/{name}/migrate\n") ||
		!strings.Contains(out.String(), "target_node=string  (required)") ||
		!strings.Contains(out.String(), "skip_checks:=boolean") {
		t.Fatalf("exit %d: %s%s", code, out.String(), errOut.String())
	}
	out.Reset()
	// A literal segment is not taken for a placeholder.
	run([]string{"api", "/networks/host-interfaces", "--schema", repoSchema}, nil, &out, io.Discard)
	if !strings.HasPrefix(out.String(), "GET /networks/host-interfaces\n") {
		t.Errorf("%s", out.String())
	}
	out.Reset()
	// Two methods of one route, a blank line between them only.
	run([]string{"api", "/vms/web1/snapshots", "--schema", repoSchema}, nil, &out, io.Discard)
	if !strings.HasPrefix(out.String(), "GET /vms/{name}/snapshots\n") || !strings.Contains(out.String(), "\n\nPOST /vms/{name}/snapshots\n") {
		t.Errorf("%q", out.String())
	}
	if code := run([]string{"api", "/nothing/here", "--schema", repoSchema}, nil, io.Discard, io.Discard); code != exitAPI {
		t.Errorf("an unknown route: exit %d", code)
	}
	errOut.Reset()
	code = run([]string{"api", "--schema", filepath.Join(t.TempDir(), "none.json")}, nil, io.Discard, &errOut)
	if code != exitAPI || !strings.Contains(errOut.String(), "--schema") {
		t.Errorf("a missing description: exit %d: %s", code, errOut.String())
	}
}
