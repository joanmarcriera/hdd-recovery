package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestLoadConfigUsesOnePublicUIPortAndLoopbackBackends(t *testing.T) {
	t.Setenv("UI_PORT", "7999")
	t.Setenv("TTYD_INTERNAL_PORT", "17681")
	t.Setenv("WEB_INTERNAL_PORT", "17788")
	t.Setenv("TTYD_PASSWORD", "secret")

	cfg := loadConfig()

	if cfg.uiPort != "7999" {
		t.Fatalf("uiPort = %q, want 7999", cfg.uiPort)
	}
	if cfg.ttydHost != "127.0.0.1" {
		t.Fatalf("ttydHost = %q, want 127.0.0.1", cfg.ttydHost)
	}
	if cfg.webHost != "127.0.0.1" {
		t.Fatalf("webHost = %q, want 127.0.0.1", cfg.webHost)
	}
	if cfg.ttydPort != "17681" {
		t.Fatalf("ttydPort = %q, want 17681", cfg.ttydPort)
	}
	if cfg.webPort != "17788" {
		t.Fatalf("webPort = %q, want 17788", cfg.webPort)
	}
}

func TestLoadConfigSplitsMultipleOllamaHosts(t *testing.T) {
	t.Setenv("OLLAMA_HOSTS", "http://ollama-a:11434, http://ollama-b:11434")

	cfg := loadConfig()

	want := []string{"http://ollama-a:11434", "http://ollama-b:11434"}
	if !reflect.DeepEqual(cfg.ollamaHosts, want) {
		t.Fatalf("ollamaHosts = %#v, want %#v", cfg.ollamaHosts, want)
	}
	if cfg.primaryOllamaHost() != "http://ollama-a:11434" {
		t.Fatalf("primaryOllamaHost = %q, want first host", cfg.primaryOllamaHost())
	}
}

func TestLoadConfigFallsBackToSingleOllamaHost(t *testing.T) {
	t.Setenv("OLLAMA_HOST", "http://ollama-one:11434")

	cfg := loadConfig()

	want := []string{"http://ollama-one:11434"}
	if !reflect.DeepEqual(cfg.ollamaHosts, want) {
		t.Fatalf("ollamaHosts = %#v, want %#v", cfg.ollamaHosts, want)
	}
}

func TestProxyRoutesTerminalAndWebSeparately(t *testing.T) {
	g.startedAt = time.Now()
	g.mu.Lock()
	g.ttydUp = true
	g.webUp = true
	g.mu.Unlock()
	t.Cleanup(func() {
		g.mu.Lock()
		g.ttydUp = false
		g.webUp = false
		g.mu.Unlock()
	})

	ttyd := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Backend", "ttyd")
		_, _ = io.WriteString(w, "ttyd:"+r.URL.Path)
	}))
	t.Cleanup(ttyd.Close)

	web := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Backend", "web")
		_, _ = io.WriteString(w, "web:"+r.URL.Path)
	}))
	t.Cleanup(web.Close)

	mux := buildMux(ttyd.URL, web.URL)
	front := httptest.NewServer(mux)
	t.Cleanup(front.Close)

	client := &http.Client{
		Timeout: 2 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}

	cases := []struct {
		path        string
		wantStatus  int
		wantBackend string
		wantBody    string
	}{
		{"/", http.StatusOK, "web", "web:/"},
		{"/db?db=x", http.StatusOK, "web", "web:/db"},
		{"/terminal/", http.StatusOK, "ttyd", "ttyd:/terminal/"},
		{"/terminal/static/foo.js", http.StatusOK, "ttyd", "ttyd:/terminal/static/foo.js"},
	}
	for _, tc := range cases {
		resp, err := client.Get(front.URL + tc.path)
		if err != nil {
			t.Fatalf("GET %s: %v", tc.path, err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != tc.wantStatus {
			t.Errorf("GET %s status = %d, want %d", tc.path, resp.StatusCode, tc.wantStatus)
		}
		if got := resp.Header.Get("X-Backend"); got != tc.wantBackend {
			t.Errorf("GET %s X-Backend = %q, want %q", tc.path, got, tc.wantBackend)
		}
		if !strings.Contains(string(body), tc.wantBody) {
			t.Errorf("GET %s body = %q, want substring %q", tc.path, body, tc.wantBody)
		}
	}

	resp, err := client.Get(front.URL + "/terminal")
	if err != nil {
		t.Fatalf("GET /terminal: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusFound {
		t.Errorf("GET /terminal status = %d, want 302", resp.StatusCode)
	}
	if loc := resp.Header.Get("Location"); loc != terminalBasePath {
		t.Errorf("GET /terminal Location = %q, want %q", loc, terminalBasePath)
	}

	resp, err = client.Get(front.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("GET /health status = %d, want 200; body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"ok":true`) {
		t.Errorf("GET /health body = %q, want ok:true", body)
	}

	resp, err = client.Get(front.URL + "/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("GET /status status = %d, want 200; body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"ttyd_up"`) {
		t.Errorf("GET /status body missing ttyd_up: %s", body)
	}
}
