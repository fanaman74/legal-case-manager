package sysinfo

import (
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var procGetProcessMemoryInfo = windows.NewLazySystemDLL("kernel32.dll").NewProc("K32GetProcessMemoryInfo")

type processMemoryCounters struct {
	CB                         uint32
	PageFaultCount             uint32
	PeakWorkingSetSize         uintptr
	WorkingSetSize             uintptr
	QuotaPeakPagedPoolUsage    uintptr
	QuotaPagedPoolUsage        uintptr
	QuotaPeakNonPagedPoolUsage uintptr
	QuotaNonPagedPoolUsage     uintptr
	PagefileUsage              uintptr
	PeakPagefileUsage          uintptr
}

func parents() map[int]int {
	out := map[int]int{}
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return out
	}
	defer windows.CloseHandle(snap)
	var e windows.ProcessEntry32
	e.Size = uint32(unsafe.Sizeof(e))
	for err = windows.Process32First(snap, &e); err == nil; err = windows.Process32Next(snap, &e) {
		out[int(e.ProcessID)] = int(e.ParentProcessID)
	}
	return out
}

func one(pid int) (time.Duration, uint64, bool) {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return 0, 0, false
	}
	defer windows.CloseHandle(h)
	var c, e, k, u windows.Filetime
	if err := windows.GetProcessTimes(h, &c, &e, &k, &u); err != nil {
		return 0, 0, false
	}
	ticks := func(f windows.Filetime) int64 { return int64(f.HighDateTime)<<32 | int64(f.LowDateTime) }
	cpu := time.Duration(ticks(k)+ticks(u)) * 100 // 100 ns units
	var m processMemoryCounters
	m.CB = uint32(unsafe.Sizeof(m))
	var mem uint64
	if r, _, _ := procGetProcessMemoryInfo.Call(uintptr(h), uintptr(unsafe.Pointer(&m)), uintptr(m.CB)); r != 0 {
		mem = uint64(m.WorkingSetSize)
	}
	return cpu, mem, true
}

func treeTotals(root int) (time.Duration, uint64, bool) {
	if _, _, ok := one(root); !ok {
		return 0, 0, false
	}
	var cpu time.Duration
	var mem uint64
	for _, pid := range descendants(root, parents()) {
		if c, m, ok := one(pid); ok {
			cpu += c
			mem += m
		}
	}
	return cpu, mem, true
}
