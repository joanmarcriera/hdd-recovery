// hdd-supervisor: process manager for the hdd-forensics container.
//
// Responsibilities:
//   - Start ttyd with password auth on a localhost-only backend
//   - Start image-serve.py on a localhost-only backend
//   - Serve one public UI port and proxy /terminal/ to ttyd
//   - Serve GET /health and GET /status for TrueNAS health probes
//   - Test configured Ollama connectivity at startup and report it in /status
//   - Emit a startup banner with access info

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	defaultUIPort       = "7788"
	defaultTtydPort     = "17681"
	defaultTtydUser     = "admin"
	defaultWebPort      = "17788"
	defaultInternalHost = "127.0.0.1"
	defaultWebRoot      = "/data/db"
	terminalBasePath    = "/terminal/"
	webPIDFile          = "/tmp/hdd-recovery-webui.pid"
	maxRestarts         = 20
	restartBackoff      = 5 * time.Second
	ollamaTimeout       = 5 * time.Second
)

type supervisorConfig struct {
	uiPort       string
	ttydHost     string
	ttydPort     string
	ttydUser     string
	ttydPassword string
	ttydCmd      string
	webHost      string
	webPort      string
	webRoot      string
	ollamaHosts  []string
}

// State shared between goroutines and the HTTP handlers.
type state struct {
	mu           sync.RWMutex
	ttydUp       bool
	ttydPID      int
	ttydRestarts int
	webUp        bool
	webPID       int
	webRestarts  int
	startedAt    time.Time
	ollamaHost   string
	ollamaHosts  []string
	ollamaStatus map[string]string
	ollamaOK     bool
	ollamaMsg    string
}

var g state

func main() {
	g.startedAt = time.Now()

	cfg := loadConfig()
	if cfg.ttydPassword == "" {
		log.Fatal("TTYD_PASSWORD environment variable is required")
	}
	if len(cfg.ollamaHosts) > 0 && os.Getenv("OLLAMA_HOST") == "" {
		_ = os.Setenv("OLLAMA_HOST", cfg.primaryOllamaHost())
	}

	g.ollamaHost = cfg.primaryOllamaHost()
	g.ollamaHosts = cfg.ollamaHosts
	g.ollamaStatus = map[string]string{}

	if len(cfg.ollamaHosts) > 0 {
		probeOllamaHosts(cfg.ollamaHosts)
	}

	printBanner(cfg)

	go runTtyd(cfg.ttydHost, cfg.ttydPort, cfg.ttydUser, cfg.ttydPassword, cfg.ttydCmd)
	go runWebUI(cfg.webHost, cfg.webPort, cfg.webRoot)

	terminalProxy := reverseProxy("http://" + cfg.ttydHost + ":" + cfg.ttydPort)
	webProxy := reverseProxy("http://" + cfg.webHost + ":" + cfg.webPort)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/status", handleStatus)
	mux.HandleFunc("/terminal", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, terminalBasePath, http.StatusFound)
	})
	mux.Handle(terminalBasePath, terminalProxy)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		webProxy.ServeHTTP(w, r)
	})

	srv := &http.Server{
		Addr:    ":" + cfg.uiPort,
		Handler: mux,
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		s := <-sig
		log.Printf("Received %s — shutting down", s)
		g.mu.Lock()
		for _, pid := range []int{g.ttydPID, g.webPID} {
			if pid > 0 {
				if proc, err := os.FindProcess(pid); err == nil {
					_ = proc.Signal(syscall.SIGTERM)
				}
			}
		}
		g.mu.Unlock()
		_ = os.Remove(webPIDFile)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(ctx)
		os.Exit(0)
	}()

	log.Printf("Unified UI listening on :%s", cfg.uiPort)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("HTTP server failed: %v", err)
	}
}

// runTtyd starts ttyd and restarts it on crash.
func runTtyd(host, port, user, password, cmdStr string) {
	credential := fmt.Sprintf("%s:%s", user, password)
	args := []string{
		"--interface", host,
		"--port", port,
		"--credential", credential,
		"--writable",
		"--base-path", terminalBasePath,
		"bash", "-lc", cmdStr,
	}

	for {
		cmd := exec.Command("ttyd", args...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr

		if err := cmd.Start(); err != nil {
			log.Printf("[ttyd] failed to start: %v — retrying in %s", err, restartBackoff)
			time.Sleep(restartBackoff)
			continue
		}

		g.mu.Lock()
		g.ttydUp = true
		g.ttydPID = cmd.Process.Pid
		g.mu.Unlock()

		log.Printf("[ttyd] started on %s:%s (pid %d)", host, port, cmd.Process.Pid)
		_ = cmd.Wait()

		g.mu.Lock()
		g.ttydUp = false
		g.ttydPID = 0
		g.ttydRestarts++
		restarts := g.ttydRestarts
		g.mu.Unlock()

		log.Printf("[ttyd] exited (restart #%d)", restarts)
		if restarts >= maxRestarts {
			log.Printf("[ttyd] crashed %d times — giving up", restarts)
			return
		}
		time.Sleep(restartBackoff)
	}
}

// runWebUI starts image-serve.py and restarts it on crash.
// It writes the PID to webPIDFile so the TUI Web Server screen can detect it.
func runWebUI(host, port, root string) {
	for {
		cmd := exec.Command("python3",
			"/root/hdd-recovery/bin/image-serve.py",
			"--host", host,
			"--port", port,
			"--root", root,
		)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr

		if err := cmd.Start(); err != nil {
			log.Printf("[webui] failed to start: %v — retrying in %s", err, restartBackoff)
			time.Sleep(restartBackoff)
			continue
		}

		pid := cmd.Process.Pid
		if err := os.WriteFile(webPIDFile, []byte(fmt.Sprintf("%d\n", pid)), 0644); err != nil {
			log.Printf("[webui] warning: could not write PID file: %v", err)
		}

		g.mu.Lock()
		g.webUp = true
		g.webPID = pid
		g.mu.Unlock()

		log.Printf("[webui] started on %s:%s (pid %d)", host, port, pid)
		_ = cmd.Wait()

		_ = os.Remove(webPIDFile)

		g.mu.Lock()
		g.webUp = false
		g.webPID = 0
		g.webRestarts++
		restarts := g.webRestarts
		g.mu.Unlock()

		log.Printf("[webui] exited (restart #%d)", restarts)
		if restarts >= maxRestarts {
			log.Printf("[webui] crashed %d times — giving up", restarts)
			return
		}
		time.Sleep(restartBackoff)
	}
}

// probeOllamaHosts tests whether configured Ollama APIs are reachable.
func probeOllamaHosts(hosts []string) {
	primaryOK := false
	primaryMsg := "not configured"
	status := map[string]string{}
	for i, host := range hosts {
		ok, msg := probeOllamaHost(host)
		status[host] = msg
		if i == 0 {
			primaryOK = ok
			primaryMsg = msg
		}
	}
	g.ollamaOK = primaryOK
	g.ollamaMsg = primaryMsg
	g.ollamaStatus = status
}

// probeOllamaHost tests whether one Ollama API is reachable.
func probeOllamaHost(host string) (bool, string) {
	client := &http.Client{Timeout: ollamaTimeout}
	tagsURL := strings.TrimRight(host, "/") + "/api/tags"
	resp, err := client.Get(tagsURL)
	if err != nil {
		msg := fmt.Sprintf("unreachable: %v", err)
		log.Printf("[ollama] %s → %s", tagsURL, msg)
		return false, msg
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
	if resp.StatusCode == http.StatusOK {
		log.Printf("[ollama] %s → OK (%s)", tagsURL, body)
		return true, "reachable"
	} else {
		msg := fmt.Sprintf("HTTP %d", resp.StatusCode)
		log.Printf("[ollama] %s → %s", tagsURL, msg)
		return false, msg
	}
}

// HTTP handlers ---------------------------------------------------------------

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	g.mu.RLock()
	up := g.ttydUp && g.webUp
	g.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	if up {
		w.WriteHeader(http.StatusOK)
		_, _ = fmt.Fprintln(w, `{"ok":true}`)
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = fmt.Fprintln(w, `{"ok":false,"error":"ui backend not running"}`)
	}
}

func handleStatus(w http.ResponseWriter, _ *http.Request) {
	g.mu.RLock()
	payload := map[string]any{
		"ok":             g.ttydUp && g.webUp,
		"ttyd_up":        g.ttydUp,
		"ttyd_pid":       g.ttydPID,
		"ttyd_restarts":  g.ttydRestarts,
		"webui_up":       g.webUp,
		"webui_pid":      g.webPID,
		"webui_restarts": g.webRestarts,
		"started_at":     g.startedAt.UTC().Format(time.RFC3339),
		"uptime_s":       int(time.Since(g.startedAt).Seconds()),
		"ollama_host":    g.ollamaHost,
		"ollama_hosts":   g.ollamaHosts,
		"ollama_status":  g.ollamaStatus,
		"ollama_ok":      g.ollamaOK,
		"ollama_msg":     g.ollamaMsg,
	}
	g.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(payload)
}

// Helpers ---------------------------------------------------------------------

func loadConfig() supervisorConfig {
	cfg := supervisorConfig{
		uiPort:       envOr("UI_PORT", defaultUIPort),
		ttydHost:     defaultInternalHost,
		ttydPort:     envOr("TTYD_INTERNAL_PORT", defaultTtydPort),
		ttydUser:     envOr("TTYD_USER", defaultTtydUser),
		ttydPassword: os.Getenv("TTYD_PASSWORD"),
		ttydCmd:      envOr("TTYD_CMD", "cd /root/hdd-recovery && exec bin/tui.sh"),
		webHost:      defaultInternalHost,
		webPort:      envOr("WEB_INTERNAL_PORT", defaultWebPort),
		webRoot:      envOr("WEB_ROOT", envOr("DB_ROOT", defaultWebRoot)),
		ollamaHosts:  parseOllamaHosts(),
	}
	return cfg
}

func (cfg supervisorConfig) primaryOllamaHost() string {
	if len(cfg.ollamaHosts) == 0 {
		return ""
	}
	return cfg.ollamaHosts[0]
}

func parseOllamaHosts() []string {
	raw := os.Getenv("OLLAMA_HOSTS")
	if raw == "" {
		raw = os.Getenv("OLLAMA_HOST")
	}
	seen := map[string]bool{}
	var hosts []string
	for _, part := range strings.Split(raw, ",") {
		host := strings.TrimSpace(part)
		if host == "" || seen[host] {
			continue
		}
		seen[host] = true
		hosts = append(hosts, host)
	}
	return hosts
}

func reverseProxy(target string) *httputil.ReverseProxy {
	u, err := url.Parse(target)
	if err != nil {
		log.Fatalf("invalid reverse proxy target %q: %v", target, err)
	}
	return httputil.NewSingleHostReverseProxy(u)
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func printBanner(cfg supervisorConfig) {
	fmt.Printf(`
┌─────────────────────────────────────────────────────────────┐
│              hdd-forensics  •  analysis container           │
├─────────────────────────────────────────────────────────────┤
│  Review UI     →  http://<host>:%s/                       │
│  Terminal      →  http://<host>:%s/terminal/              │
│  Health        →  http://<host>:%s/health                 │
│  Status        →  http://<host>:%s/status                 │
│  DB root       →  %-40s │
`, cfg.uiPort, cfg.uiPort, cfg.uiPort, cfg.uiPort, cfg.webRoot)

	for _, host := range g.ollamaHosts {
		status := g.ollamaStatus[host]
		if status == "" {
			status = "not probed"
		}
		fmt.Printf("│  Ollama        →  %-40s │\n", host+" "+status)
	}

	fmt.Println("└─────────────────────────────────────────────────────────────┘")
}
