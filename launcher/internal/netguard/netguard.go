// Package netguard decides which addresses the Control Center listens on and
// which clients may connect. By default only the host itself can connect;
// the Admin can opt in to private LAN addresses. Public addresses are never
// allowed.
package netguard

import (
	"net"
	"net/http"
	"sort"
	"strings"
)

var privateNets = func() []*net.IPNet {
	var out []*net.IPNet
	for _, c := range []string{"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10"} {
		_, n, _ := net.ParseCIDR(c)
		out = append(out, n)
	}
	return out
}()

// IsPrivate reports whether ip is in a private (RFC 1918 / ULA / link-local) range.
func IsPrivate(ip net.IP) bool {
	for _, n := range privateNets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// Allowed reports whether a client at ip may connect.
func Allowed(ip net.IP, lan bool) bool {
	if ip == nil {
		return false
	}
	if ip.IsLoopback() {
		return true
	}
	return lan && IsPrivate(ip)
}

// ClientIP extracts the peer address. Forwarding headers are ignored: the
// launcher is never behind a proxy.
func ClientIP(r *http.Request) net.IP {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return nil
	}
	return net.ParseIP(host)
}

// virtualPrefixes name adapters that belong to Docker, WSL, Hyper-V or VM
// software. Their addresses are not reachable from other devices, so they are
// never offered as the address to share.
var virtualPrefixes = []string{"docker", "br-", "veth", "virbr", "vmnet", "vboxnet", "cni", "flannel", "tailscale", "utun", "zt", "vethernet", "hyper-v", "vmware", "virtualbox"}

// IsVirtual reports whether an interface name looks like a virtual adapter.
func IsVirtual(name string) bool {
	n := strings.ToLower(name)
	for _, p := range virtualPrefixes {
		if strings.HasPrefix(n, p) {
			return true
		}
	}
	return false
}

// LANAddresses returns the host's private IPv4 addresses on physical
// interfaces that are up, sorted for stable display.
func LANAddresses() []net.IP {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []net.IP
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagUp == 0 || ifc.Flags&net.FlagLoopback != 0 || IsVirtual(ifc.Name) {
			continue
		}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			ipn, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			if v4 := ipn.IP.To4(); v4 != nil && IsPrivate(v4) && !v4.IsLinkLocalUnicast() {
				out = append(out, v4)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].String() < out[j].String() })
	return out
}
