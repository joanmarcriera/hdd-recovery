package main

import (
	"reflect"
	"testing"
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
