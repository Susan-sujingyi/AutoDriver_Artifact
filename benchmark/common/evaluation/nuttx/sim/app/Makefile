include $(APPDIR)/Make.defs

PROGNAME = bench
PRIORITY = 100
STACKSIZE = 32768
MODULE = $(CONFIG_SYSTEM_BENCH_EVALUATOR)
MAINSRC = entry.c
CSRCS = $(filter-out entry.c,$(wildcard *.c))
EXTOBJS += $(wildcard candidate.o)

include $(APPDIR)/Application.mk
