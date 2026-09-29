package netguard

import (
	"net"
	"testing"
)

func TestAllowed(t *testing.T) {
	cases := []struct {
		ip     string
		lanOff bool
		lanOn  bool
	}{
		{"127.0.0.1", true, true},
		{"::1", true, true},
		{"192.168.1.20", false, true},
		{"10.4.5.6", false, true},
		{"172.20.0.9", false, true},
		{"172.32.0.1", false, false}, // outside 172.16/12
		{"8.8.8.8", false, false},
		{"100.64.0.1", false, false}, // CGNAT is not LAN
		{"2001:db8::1", false, false},
	}
	for _, c := range cases {
		ip := net.ParseIP(c.ip)
		if Allowed(ip, false) != c.lanOff {
			t.Errorf("%s with LAN off: got %v", c.ip, !c.lanOff)
		}
		if Allowed(ip, true) != c.lanOn {
			t.Errorf("%s with LAN on: got %v", c.ip, !c.lanOn)
		}
	}
	if Allowed(nil, true) {
		t.Error("nil IP must be rejected")
	}
}

func TestIsVirtual(t *testing.T) {
	for _, n := range []string{"docker0", "br-1a2b3c", "veth12ab", "vEthernet (WSL)", "vEthernet (Default Switch)", "VirtualBox Host-Only Network"} {
		if !IsVirtual(n) {
			t.Errorf("%s should be virtual", n)
		}
	}
	for _, n := range []string{"eth0", "en0", "Ethernet", "Wi-Fi", "wlan0"} {
		if IsVirtual(n) {
			t.Errorf("%s should not be virtual", n)
		}
	}
}
