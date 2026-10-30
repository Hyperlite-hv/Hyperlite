package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"text/tabwriter"
)

// printAnswer prints the API's answer: as it is with --json, else a table for a list and one line per field for an
// object.
func printAnswer(w io.Writer, raw json.RawMessage, asJSON bool) error {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return nil
	}
	if asJSON {
		_, err := fmt.Fprintln(w, string(indent(raw)))
		return err
	}
	// Numbers stay as the API wrote them: a size in bytes printed as a float reads 4.294967296e+09.
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		_, err = fmt.Fprintln(w, string(raw))
		return err
	}
	switch x := v.(type) {
	case map[string]any:
		if items, ok := x["items"].([]any); ok {
			var list struct {
				Items []json.RawMessage `json:"items"`
			}
			_ = json.Unmarshal(raw, &list)
			printTable(w, items, list.Items)
			return nil
		}
		printObject(w, x)
	case []any:
		var list []json.RawMessage
		_ = json.Unmarshal(raw, &list)
		printTable(w, x, list)
	default:
		fmt.Fprintln(w, cell(x))
	}
	return nil
}

func indent(raw json.RawMessage) []byte {
	var out bytes.Buffer
	if json.Indent(&out, raw, "", "  ") != nil {
		return raw
	}
	return out.Bytes()
}

// printTable prints a list of objects with one column per field of the first that is not itself a list or an object,
// in the API's order; anything else as one value per line.
func printTable(w io.Writer, items []any, rawItems []json.RawMessage) {
	if len(items) == 0 {
		fmt.Fprintln(w, "(none)")
		return
	}
	first, ok := items[0].(map[string]any)
	if !ok {
		for _, it := range items {
			fmt.Fprintln(w, cell(it))
		}
		return
	}
	var cols []string
	order := sortedKeys(first)
	if len(rawItems) > 0 {
		if keys := objectKeys(rawItems[0]); len(keys) == len(first) {
			order = keys
		}
	}
	for _, k := range order {
		switch first[k].(type) {
		case map[string]any, []any:
			continue
		}
		cols = append(cols, k)
	}
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, strings.ToUpper(strings.Join(cols, "\t")))
	for _, it := range items {
		row, _ := it.(map[string]any)
		cells := make([]string, len(cols))
		for i, c := range cols {
			cells[i] = cell(row[c])
		}
		fmt.Fprintln(tw, strings.Join(cells, "\t"))
	}
	tw.Flush()
}

func printObject(w io.Writer, obj map[string]any) {
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	for _, k := range sortedKeys(obj) {
		fmt.Fprintf(tw, "%s:\t%s\n", k, cell(obj[k]))
	}
	tw.Flush()
}

func cell(v any) string {
	switch x := v.(type) {
	case nil:
		return "-"
	case string:
		return strings.ReplaceAll(x, "\n", " ")
	case json.Number, bool:
		return fmt.Sprint(x)
	default:
		data, _ := json.Marshal(x)
		return string(data)
	}
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// objectKeys is the fields of a JSON object in the order the API wrote them, which a Go map loses: the API puts the
// name first, the column a person reads first.
func objectKeys(raw json.RawMessage) []string {
	dec := json.NewDecoder(bytes.NewReader(raw))
	if t, err := dec.Token(); err != nil || t != json.Delim('{') {
		return nil
	}
	var keys []string
	for dec.More() {
		t, err := dec.Token()
		if err != nil {
			return nil
		}
		key, _ := t.(string)
		keys = append(keys, key)
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			return nil
		}
	}
	return keys
}
