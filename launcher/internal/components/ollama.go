package components

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// ollamaClient talks to the local models service on loopback.
type ollamaClient struct {
	base   string
	client *http.Client
}

func newOllamaClient(base string) *ollamaClient {
	// Loopback only: never go through a proxy.
	return &ollamaClient{base: base, client: &http.Client{Transport: &http.Transport{Proxy: nil}}}
}

// pull downloads a model and reports progress until Ollama says it's done.
func (o *ollamaClient) pull(ctx context.Context, model string, progress func(done, total int64)) error {
	body, _ := json.Marshal(map[string]any{"model": model, "stream": true})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, o.base+"/api/pull", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := o.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("models service answered %s", resp.Status)
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	var last time.Time
	for sc.Scan() {
		var ev struct {
			Status    string `json:"status"`
			Error     string `json:"error"`
			Total     int64  `json:"total"`
			Completed int64  `json:"completed"`
		}
		if err := json.Unmarshal(sc.Bytes(), &ev); err != nil {
			continue
		}
		if ev.Error != "" {
			return errors.New(ev.Error)
		}
		if ev.Status == "success" {
			return nil
		}
		if ev.Total > 0 && time.Since(last) >= time.Second {
			last = time.Now()
			progress(ev.Completed, ev.Total)
		}
	}
	if err := sc.Err(); err != nil {
		return err
	}
	return errors.New("the models service stopped before the download finished")
}
