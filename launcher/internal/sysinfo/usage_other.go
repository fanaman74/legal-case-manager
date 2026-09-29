//go:build !linux && !windows

package sysinfo

import "time"

func treeTotals(int) (time.Duration, uint64, bool) { return 0, 0, false }
