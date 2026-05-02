// hdd-supervisor: process manager for the hdd-forensics container.
//
// Responsibilities:
//   - Start ttyd with password auth and restart it on crash
//   - Start image-serve.py (read-only web UI) and restart it on crash
//   - Serve GET /health and GET /status for TrueNAS health probes
//   - Test Ollama connectivity at startup and report it in /status
//   - Emit a startup banner with access info

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"sync"
	"syscall"
	"time"
)

const (
	defaultTtydPort   = "7681"
	defaultHealthPort = "8080"
	defaultTtydUser   = "admin"
	defaultWebPort    = "7788"
	defaultWebHost    = "0.0.0.0"
	defaultWebRoot    = "/mnt/recovery16tb/recovery"
	webPIDFile        = "/tmp/hdd-recovery-webui.pid"
	maxRestarts       = 20
	restartBackoff    = 5 * time.Second
	ollamaTimeout     = 5 * time.Second
)

// State shared between goroutines and the HTTP handlers.
type state struct {
	mu          sync.RWMutex
	ttydUp      bool
	ttydPID     int
	ttydRestarts int
	webUp       bool
	webPID      int
	webRestarts int
	startedAt   time.Time
	ollamaHost  string
	ollamaOK    bool
	ollamaMsg   string
}

var g state

func main() {
	g.startedAt = time.Now()

	password := os.Getenv("TTYD_PASSWORD")
	if password == "" {
		log.Fatal("TTYD_PASSWORD environment variable is required")
	}

	ttydPort  := envOr("TTYD_PORT",  defaultTtydPort)
	ttydUser  := envOr("TTYD_USER",  defaultTtydUser)
	healthPort := envOr("HEALTH_PORT", defaultHealthPort)
	webPort   := envOr("WEB_PORT",   defaultWebPort)
	webHost   := envOr("WEB_HOST",   defaultWebHost)
	webRoot   := envOr("WEB_ROOT",   defaultWebRoot)
	g.ollamaHost = envOr("OLLAMA_HOST", "")
	ttydCmd   := envOr("TTYD_CMD", "/bin/bash -c 'cd /root/hdd-recovery && exec bin/tui.sh'")

	if g.ollamaHost != "" {
		probeOllama()
	}

	printBanner(ttydPort, webPort, healthPort)

	go runTtyd(ttydPort, ttydUser, password, ttydCmd)
	go runWebUI(webHost, webPort, webRoot)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/status", handleStatus)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/" {
			http.Redirect(w, r, "/health", http.StatusFound)
			return
		}
		http.NotFound(w, r)
	})

	srv := &http.Server{
		Addr:    ":" + healthPort,
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

	log.Printf("Health/status API listening on :%s", healthPort)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("HTTP server failed: %v", err)
	}
}

// runTtyd starts ttyd and restarts it on crash.
func runTtyd(port, user, password, cmdStr string) {
	credential := fmt.Sprintf("%s:%s", user, password)
	args := []string{
		"--port", port,
		"--credential", credential,
		"--writable",
		"bash", "-c", cmdStr,
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

		log.Printf("[ttyd] started on port %s (pid %d)", port, cmd.Process.Pid)
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

// probeOllama tests whether the Ollama API is reachable.
func probeOllama() {
	client := &http.Client{Timeout: ollamaTimeout}
	url := g.ollamaHost + "/api/tags"
	resp, err := client.Get(url)
	if err != nil {
		g.ollamaOK = false
		g.ollamaMsg = fmt.Sprintf("unreachable: %v", err)
		log.Printf("[ollama] %s → %s", url, g.ollamaMsg)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
	if resp.StatusCode == http.StatusOK {
		g.ollamaOK = true
		g.ollamaMsg = "reachable"
		log.Printf("[ollama] %s → OK (%s)", url, body)
	} else {
		g.ollamaOK = false
		g.ollamaMsg = fmt.Sprintf("HTTP %d", resp.StatusCode)
		log.Printf("[ollama] %s → %s", url, g.ollamaMsg)
	}
}

// HTTP handlers ---------------------------------------------------------------

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	g.mu.RLock()
	up := g.ttydUp
	g.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	if up {
		w.WriteHeader(http.StatusOK)
		_, _ = fmt.Fprintln(w, `{"ok":true}`)
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = fmt.Fprintln(w, `{"ok":false,"error":"ttyd not running"}`)
	}
}

func handleStatus(w http.ResponseWriter, _ *http.Request) {
	g.mu.RLock()
	payload := map[string]any{
		"ok":            g.ttydUp,
		"ttyd_up":       g.ttydUp,
		"ttyd_pid":      g.ttydPID,
		"ttyd_restarts": g.ttydRestarts,
		"webui_up":      g.webUp,
		"webui_pid":     g.webPID,
		"webui_restarts": g.webRestarts,
		"started_at":    g.startedAt.UTC().Format(time.RFC3339),
		"uptime_s":      int(time.Since(g.startedAt).Seconds()),
		"ollama_host":   g.ollamaHost,
		"ollama_ok":     g.ollamaOK,
		"ollama_msg":    g.ollamaMsg,
	}
	g.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(payload)
}

// Helpers ---------------------------------------------------------------------

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func printBanner(ttydPort, webPort, healthPort string) {
	fmt.Printf(`
┌─────────────────────────────────────────────────────────────┐
│              hdd-forensics  •  analysis container           │
├─────────────────────────────────────────────────────────────┤
│  Terminal UI   →  http://<host>:%s                        │
│  Web query UI  →  http://<host>:%s                        │
│  Health API    →  http://<host>:%s/health                │
│  Status API    →  http://<host>:%s/status                │
`, ttydPort, webPort, healthPort, healthPort)

	if g.ollamaHost != "" {
		status := "✗ unreachable"
		if g.ollamaOK {
			status = "✓ reachable"
		}
		fmt.Printf("│  Ollama        →  %-40s │\n", g.ollamaHost+" "+status)
	}

	fmt.Println("└─────────────────────────────────────────────────────────────┘")
}
