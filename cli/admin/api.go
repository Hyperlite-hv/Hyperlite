package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/Hyperlite-hv/Hyperlite/cli/hyperlite"
)

// defaultSchema is the OpenAPI description of API v1 the package ships next to the code (installer/build-deb.sh): the
// server's /openapi.json takes the documentation's own sign-in, not a token, and the binary and the API come from the
// same commit, so this file is the node's own contract.
const defaultSchema = "/usr/lib/hyperlite/docs/api/openapi-v1.json"

type openAPI struct {
	Paths      map[string]map[string]operation `json:"paths"`
	Components struct {
		Schemas map[string]schema `json:"schemas"`
	} `json:"components"`
}

type operation struct {
	Summary     string      `json:"summary"`
	Description string      `json:"description"`
	Parameters  []parameter `json:"parameters"`
	RequestBody *struct {
		Required bool                               `json:"required"`
		Content  map[string]struct{ Schema schema } `json:"content"`
	} `json:"requestBody"`
}

type parameter struct {
	Name     string `json:"name"`
	In       string `json:"in"`
	Required bool   `json:"required"`
	Schema   schema `json:"schema"`
}

type schema struct {
	Ref         string            `json:"$ref"`
	Type        string            `json:"type"`
	Enum        []any             `json:"enum"`
	AnyOf       []schema          `json:"anyOf"`
	Items       *schema           `json:"items"`
	Properties  map[string]schema `json:"properties"`
	Required    []string          `json:"required"`
	Default     any               `json:"default"`
	Description string            `json:"description"`
}

func loadSchema(path string) (*openAPI, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("the API's description cannot be read (%v): give it with --schema docs/api/openapi-v1.json", err)
	}
	var doc openAPI
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("%s is not an OpenAPI description: %v", path, err)
	}
	return &doc, nil
}

var methodOrder = []string{"get", "post", "put", "patch", "delete"}

// showAPI lists the routes, the routes under a path ending in "/", or what one route takes: a concrete path
// (/vms/web1/start) finds its template (/vms/{name}/start).
func showAPI(o options, w io.Writer) error {
	doc, err := loadSchema(o.schema)
	if err != nil {
		return err
	}
	paths := make([]string, 0, len(doc.Paths))
	for p := range doc.Paths {
		paths = append(paths, strings.TrimPrefix(p, hyperlite.APIPrefix))
	}
	sort.Strings(paths)
	if len(o.rest) == 0 || strings.HasSuffix(o.rest[0], "/") {
		prefix := ""
		if len(o.rest) > 0 {
			prefix = "/" + strings.Trim(strings.TrimPrefix(o.rest[0], hyperlite.APIPrefix), "/") + "/"
		}
		n := 0
		for _, p := range paths {
			if strings.HasPrefix(p+"/", prefix) {
				fmt.Fprintf(w, "%-26s %s\n", strings.Join(methods(doc.Paths[hyperlite.APIPrefix+p]), " "), p)
				n++
			}
		}
		if n == 0 {
			return &exitError{exitAPI, fmt.Errorf("no route under %s", prefix)}
		}
		return nil
	}
	want := "/" + strings.Trim(strings.TrimPrefix(strings.SplitN(o.rest[0], "?", 2)[0], hyperlite.APIPrefix), "/")
	for _, p := range paths {
		if matches(p, want) {
			printRoute(w, doc, p)
			return nil
		}
	}
	return &exitError{exitAPI, fmt.Errorf("no route %s in API v1: hyperlite-admin api %s/ lists what is under it", want, want)}
}

func methods(ops map[string]operation) []string {
	var out []string
	for _, m := range methodOrder {
		if _, ok := ops[m]; ok {
			out = append(out, strings.ToUpper(m))
		}
	}
	return out
}

// matches tells whether a concrete path is an instance of a template; a literal segment wins over a placeholder
// because the paths are tried in order and "/vms/host-interfaces" sorts with its literal.
func matches(template, path string) bool {
	t, p := strings.Split(template, "/"), strings.Split(path, "/")
	if len(t) != len(p) {
		return false
	}
	for i := range t {
		if t[i] != p[i] && !(strings.HasPrefix(t[i], "{") && strings.HasSuffix(t[i], "}")) {
			return false
		}
	}
	return true
}

func printRoute(w io.Writer, doc *openAPI, path string) {
	ops := doc.Paths[hyperlite.APIPrefix+path]
	printed := 0
	for _, m := range methodOrder {
		op, ok := ops[m]
		if !ok {
			continue
		}
		if printed > 0 {
			fmt.Fprintln(w) // between two methods of the route, not before the first
		}
		printed++
		fmt.Fprintf(w, "%s %s\n", strings.ToUpper(m), path)
		if line := firstLine(op.Description, op.Summary); line != "" {
			fmt.Fprintf(w, "  %s\n", line)
		}
		for _, p := range op.Parameters {
			if p.In == "query" {
				fmt.Fprintf(w, "  %s==%s%s\n", p.Name, typeOf(doc, p.Schema), requiredMark(p.Required))
			}
		}
		if op.RequestBody == nil {
			continue
		}
		for _, c := range op.RequestBody.Content {
			body := resolve(doc, c.Schema)
			fields := make([]string, 0, len(body.Properties))
			for f := range body.Properties {
				fields = append(fields, f)
			}
			sort.Strings(fields)
			for _, f := range fields {
				sep := ":="
				if typeOf(doc, body.Properties[f]) == "string" {
					sep = "="
				}
				fmt.Fprintf(w, "  %s%s%s%s\n", f, sep, typeOf(doc, body.Properties[f]), requiredMark(contains(body.Required, f)))
			}
			break
		}
	}
}

func firstLine(texts ...string) string {
	for _, t := range texts {
		if t = strings.TrimSpace(t); t != "" {
			line, _, _ := strings.Cut(t, "\n")
			return line
		}
	}
	return ""
}

func requiredMark(required bool) string {
	if required {
		return "  (required)"
	}
	return ""
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func resolve(doc *openAPI, s schema) schema {
	for depth := 0; s.Ref != "" && depth < 8; depth++ {
		s = doc.Components.Schemas[strings.TrimPrefix(s.Ref, "#/components/schemas/")]
	}
	return s
}

// typeOf is a schema in a word or two: string, integer, boolean, a|b for an enum, list of X, X|null.
func typeOf(doc *openAPI, s schema) string {
	s = resolve(doc, s)
	switch {
	case len(s.Enum) > 0:
		parts := make([]string, len(s.Enum))
		for i, e := range s.Enum {
			parts[i] = fmt.Sprint(e)
		}
		return strings.Join(parts, "|")
	case len(s.AnyOf) > 0:
		parts := make([]string, 0, len(s.AnyOf))
		for _, a := range s.AnyOf {
			parts = append(parts, typeOf(doc, a))
		}
		return strings.Join(parts, "|")
	case s.Type == "array" && s.Items != nil:
		return "list of " + typeOf(doc, *s.Items)
	case s.Type == "" && len(s.Properties) > 0:
		return "object"
	case s.Type == "":
		return "any"
	}
	return s.Type
}
