package sysinfo

import (
	"os"
	"strconv"
	"strings"
	"time"
)

const clockTicks = 100 // USER_HZ on every mainstream Linux build

func procStat(pid int) (ppid int, cpu time.Duration, ok bool) {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return 0, 0, false
	}
	// The command name is in parentheses and may contain spaces.
	s := string(b)
	i := strings.LastIndexByte(s, ')')
	if i < 0 {
		return 0, 0, false
	}
	f := strings.Fields(s[i+1:])
	if len(f) < 13 {
		return 0, 0, false
	}
	ppid, _ = strconv.Atoi(f[1])
	ut, _ := strconv.ParseUint(f[11], 10, 64)
	st, _ := strconv.ParseUint(f[12], 10, 64)
	return ppid, time.Duration(ut+st) * time.Second / clockTicks, true
}

func rss(pid int) uint64 {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/statm")
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b))
	if len(f) < 2 {
		return 0
	}
	pages, _ := strconv.ParseUint(f[1], 10, 64)
	return pages * uint64(os.Getpagesize())
}

func treeTotals(root int) (time.Duration, uint64, bool) {
	if _, _, ok := procStat(root); !ok {
		return 0, 0, false
	}
	parent := map[int]int{}
	ents, _ := os.ReadDir("/proc")
	for _, e := range ents {
		pid, err := strconv.Atoi(e.Name())
		if err != nil {
			continue
		}
		if pp, _, ok := procStat(pid); ok {
			parent[pid] = pp
		}
	}
	var cpu time.Duration
	var mem uint64
	for _, pid := range descendants(root, parent) {
		if _, c, ok := procStat(pid); ok {
			cpu += c
			mem += rss(pid)
		}
	}
	return cpu, mem, true
}
