package hyperlite

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// APIVersion is the version of the API this package speaks.
const APIVersion = 1

// Health is the answer of GET /health. HyperliteVersion is only given to a signed-in caller.
type Health struct {
	Status           string `json:"status"`
	API              int    `json:"api"`
	HyperliteVersion string `json:"hyperlite_version"`
	// Node is this node's name in its cluster (signed-in callers): lists name this node's objects "local", other
	// members' by their name, and ?node= takes either for this node.
	Node string `json:"node"`
}

// GetHealth reads GET /health, which lives at the root, outside the API's prefix.
func (c *Client) GetHealth(ctx context.Context) (*Health, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", c.URL+"/health", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if c.UserAgent != "" {
		req.Header.Set("User-Agent", c.UserAgent)
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	resp, err := c.HTTPClient().Do(req)
	if err != nil {
		return nil, fmt.Errorf("cannot reach %s: %w", c.URL, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, &APIError{Status: resp.StatusCode, Detail: fmt.Sprintf("GET /health answered HTTP %d", resp.StatusCode)}
	}
	var h Health
	if err := json.Unmarshal(data, &h); err != nil {
		return nil, fmt.Errorf("unreadable answer of %s/health: %w", c.URL, err)
	}
	return &h, nil
}

// CheckAPI fails when the server does not speak the API of this package. A server before 1.0 has no "api" field.
func (h *Health) CheckAPI() error {
	switch {
	case h.API == 0:
		return fmt.Errorf("this server runs a Hyperlite version before 1.0, which has no API v%d: update it", APIVersion)
	case h.API != APIVersion:
		return fmt.Errorf("this client speaks API v%d; this server speaks API v%d", APIVersion, h.API)
	}
	return nil
}

// AtLeast tells whether the server's version is min or later (both "major.minor.patch"; a suffix such as "-rc1" is
// ignored). An unknown version (a caller that is not signed in) is not at least anything.
func (h *Health) AtLeast(min string) bool {
	have, ok1 := parseVersion(h.HyperliteVersion)
	want, ok2 := parseVersion(min)
	if !ok1 || !ok2 {
		return false
	}
	for i := range have {
		if have[i] != want[i] {
			return have[i] > want[i]
		}
	}
	return true
}

func parseVersion(v string) ([3]int, bool) {
	var out [3]int
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	if i := strings.IndexAny(v, "-+ "); i >= 0 {
		v = v[:i]
	}
	parts := strings.Split(v, ".")
	if len(parts) != 3 {
		return out, false
	}
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return out, false
		}
		out[i] = n
	}
	return out, true
}
