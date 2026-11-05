// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package phonetest is an in-memory stand-in for the daemon's phone routes,
// shaped like src/gaia/phone/routes.py, so UI tests can walk real flows.
package phonetest

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/phone"
)

// Code is the one code every verification accepts.
const Code = "123456"

// Request is one call the fake received.
type Request struct {
	Method, Path, Body string
}

// Fake is the phone service. Lock Mu to change its state from a test.
type Fake struct {
	Mu       sync.Mutex
	Settings map[string]any
	Contacts []phone.Contact
	Alerts   []phone.Alert
	Sim      phone.SimLog
	// KeysError, when set, is the refusal POST /keys answers with.
	KeysError *phone.Error
	// Missing makes every route a 404, like a daemon without phone alerts.
	Missing bool

	requests []Request
	srv      *httptest.Server
	nextID   int
	pending  map[string]pending
}

type pending struct {
	kind, number, label string
	contactID           int
	confirm             bool
	attempts            int
}

// New starts a fake; it stops with the test.
func New(t *testing.T) *Fake {
	f := &Fake{
		Settings: map[string]any{
			"enabled": false, "provider": "simulated", "from_number": "",
			"texts_enabled": false, "quiet_hours": nil, "keep_awake": true,
			"limits": map[string]any{"calls_per_day": 6, "texts_per_day": 20, "agent_calls_per_day": 2},
			"rules": map[string]any{
				"schedule_failed":  map[string]any{"on": true, "steps": "notify_text_call", "urgent": false},
				"approval_waiting": map[string]any{"on": true, "steps": "notify_text", "urgent": false, "after_minutes": 10},
				"gaia_down":        map[string]any{"on": true, "steps": "notify_text_call", "urgent": false},
				"agent":            map[string]any{"on": false, "steps": "notify_text_call", "urgent": false},
			},
		},
		nextID:  1,
		pending: map[string]pending{},
	}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

// Client reaches this fake.
func (f *Fake) Client() *phone.Client { return phone.NewHTTP(f.srv.URL, "test-token") }

// Requests is every call so far, in order.
func (f *Fake) Requests() []Request {
	f.Mu.Lock()
	defer f.Mu.Unlock()
	return append([]Request(nil), f.requests...)
}

// Last is the newest call to method path, or a zero Request.
func (f *Fake) Last(method, path string) Request {
	reqs := f.Requests()
	for i := len(reqs) - 1; i >= 0; i-- {
		if reqs[i].Method == method && reqs[i].Path == path {
			return reqs[i]
		}
	}
	return Request{}
}

// AddAlert opens an alert.
func (f *Fake) AddAlert(title string) int {
	f.Mu.Lock()
	defer f.Mu.Unlock()
	id := f.nextID
	f.nextID++
	f.Alerts = append([]phone.Alert{{ID: id, Kind: "schedule_failed", Title: title, Count: 1, State: "open"}}, f.Alerts...)
	return id
}

func (f *Fake) handle(w http.ResponseWriter, r *http.Request) {
	raw, _ := io.ReadAll(r.Body)
	f.Mu.Lock()
	defer f.Mu.Unlock()
	path := strings.TrimPrefix(r.URL.Path, phone.Prefix)
	f.requests = append(f.requests, Request{Method: r.Method, Path: path, Body: string(raw)})
	if f.Missing {
		writeJSON(w, 404, map[string]any{"detail": "Not Found"})
		return
	}
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	route := r.Method + " " + path
	switch {
	case route == "GET /status":
		writeJSON(w, 200, f.status())
	case route == "GET /settings":
		writeJSON(w, 200, f.Settings)
	case route == "PUT /settings":
		if on, ok := body["enabled"].(bool); ok && on && (len(f.Contacts) == 0 || f.Settings["from_number"] == "") {
			fail(w, 409, "not_configured", "Add your number to the phone book before turning phone on.")
			return
		}
		merge(f.Settings, body)
		writeJSON(w, 200, f.Settings)
	case route == "POST /simulated":
		f.Settings["provider"], f.Settings["from_number"] = "simulated", "+15550100000"
		writeJSON(w, 200, f.status())
	case route == "POST /keys":
		if f.KeysError != nil {
			fail(w, 422, f.KeysError.Code, f.KeysError.Message)
			return
		}
		f.Settings["provider"], f.Settings["from_number"] = "twilio", body["from_number"]
		writeJSON(w, 200, f.status())
	case route == "DELETE /keys":
		f.Settings["enabled"], f.Settings["from_number"] = false, ""
		writeJSON(w, 200, f.status())
	case route == "GET /simulated/log":
		writeJSON(w, 200, f.Sim)
	case route == "POST /contacts":
		number, _ := body["number"].(string)
		label, _ := body["label"].(string)
		vid := strings.Repeat(strconv.Itoa(len(f.pending)), 32)[:32]
		confirm := len(f.Contacts) > 0
		f.pending[vid] = pending{kind: "add", number: number, label: label, confirm: confirm}
		out := map[string]any{"verification_id": vid, "number": phone.Mask(number), "needs_confirm_code": confirm, "expires_in": 600}
		if confirm {
			out["confirm_sent_to"] = f.Contacts[0].Number
		}
		writeJSON(w, 200, out)
	case route == "POST /contacts/confirm":
		vid, _ := body["verification_id"].(string)
		p, ok := f.pending[vid]
		if !ok {
			fail(w, 404, "not_found", "That code request wasn't found. Start again.")
			return
		}
		if body["code"] != Code || (p.confirm && body["confirm_code"] != Code) {
			p.attempts++
			f.pending[vid] = p
			fail(w, 422, "verification_failed", fmt.Sprintf("That code isn't right. %d tries left.", 5-p.attempts))
			return
		}
		delete(f.pending, vid)
		c := phone.Contact{ID: f.nextID, Label: p.label, Number: phone.Mask(p.number), Primary: len(f.Contacts) == 0}
		f.nextID++
		f.Contacts = append(f.Contacts, c)
		writeJSON(w, 200, c)
	case r.Method == "POST" && strings.HasPrefix(path, "/contacts/") && strings.HasSuffix(path, "/remove"):
		id, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(path, "/contacts/"), "/remove"))
		for _, c := range f.Contacts {
			if c.ID == id {
				vid := strings.Repeat("r", 32)
				f.pending[vid] = pending{kind: "remove", contactID: id}
				writeJSON(w, 200, map[string]any{"verification_id": vid, "number": c.Number})
				return
			}
		}
		fail(w, 404, "not_found", "That number isn't in the phone book.")
	case route == "POST /contacts/remove/confirm":
		vid, _ := body["verification_id"].(string)
		p, ok := f.pending[vid]
		if !ok || body["code"] != Code {
			fail(w, 422, "verification_failed", "That code isn't right. 4 tries left.")
			return
		}
		delete(f.pending, vid)
		kept := f.Contacts[:0]
		for _, c := range f.Contacts {
			if c.ID != p.contactID {
				kept = append(kept, c)
			}
		}
		f.Contacts = kept
		writeJSON(w, 200, f.status())
	case route == "GET /alerts" || route == "GET /alerts?open=true":
		var out []phone.Alert
		for _, a := range f.Alerts {
			if r.URL.Query().Get("open") != "true" || a.State == "open" {
				out = append(out, a)
			}
		}
		writeJSON(w, 200, map[string]any{"alerts": out})
	case r.Method == "POST" && strings.HasPrefix(path, "/alerts/") && strings.HasSuffix(path, "/handled"):
		id, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(path, "/alerts/"), "/handled"))
		for i := range f.Alerts {
			if f.Alerts[i].ID == id {
				f.Alerts[i].State = "handled"
				writeJSON(w, 200, f.Alerts[i])
				return
			}
		}
		fail(w, 404, "not_found", "That alert wasn't found.")
	case route == "POST /test":
		if f.Settings["enabled"] != true {
			fail(w, 409, "phone_off", "Turn phone on to send a test alert.")
			return
		}
		a := phone.Alert{ID: f.nextID, Kind: "test", Title: "Test alert", Count: 1, State: "open", Urgent: true,
			Deliveries: []phone.Delivery{{Channel: "call", Number: f.Contacts[0].Number, State: "placed"}}}
		f.nextID++
		f.Alerts = append([]phone.Alert{a}, f.Alerts...)
		f.Sim.Calls = append(f.Sim.Calls, phone.SimCall{To: "+15551234567", Script: "This is GAIA on your PC. This is a test alert."})
		writeJSON(w, 200, map[string]any{"alert": a})
	default:
		writeJSON(w, 404, map[string]any{"detail": "Not Found"})
	}
}

func (f *Fake) status() map[string]any {
	open := 0
	for _, a := range f.Alerts {
		if a.State == "open" {
			open++
		}
	}
	contacts := f.Contacts
	if contacts == nil {
		contacts = []phone.Contact{}
	}
	return map[string]any{
		"enabled": f.Settings["enabled"], "provider": f.Settings["provider"],
		"configured": f.Settings["from_number"] != "", "from_number": f.Settings["from_number"],
		"texts_enabled": f.Settings["texts_enabled"], "contacts": contacts, "open_alerts": open,
		"calls_today": 0, "texts_today": 0, "last_error": nil,
		"awake": map[string]any{"held": f.Settings["enabled"], "reason": "off"},
	}
}

// merge applies a settings patch the way the service does: nested objects
// update, everything else replaces.
func merge(dst, patch map[string]any) {
	for k, v := range patch {
		sub, ok := v.(map[string]any)
		cur, curOK := dst[k].(map[string]any)
		if ok && curOK && k != "quiet_hours" {
			merge(cur, sub)
			continue
		}
		dst[k] = v
	}
}

func fail(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"detail": map[string]any{"code": code, "message": message}})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
