# gpu-boost/tools/Makefile — build the FP32 load generator and run quick checks.
#
#   make            build gpu_burn into build/ (ignored by git)
#   make check      run the 15 s boost health check (no root needed)
#   make diagnose   run the no-sudo deep diagnosis
#   make clean      remove build/
#
# Build output goes to build/, NOT to the tracked prebuilt gpu_burn binary, so
# running make never dirties the repository. verify-boost.sh does not need this:
# it compiles its own private temporary copy if no executable gpu_burn exists.
#
# Requires gcc and OpenCL development headers (libopencl / opencl-headers).
CC       ?= gcc
CFLAGS   ?= -O2
LDLIBS   ?= -lOpenCL
BUILDDIR ?= build

all: $(BUILDDIR)/gpu_burn

$(BUILDDIR)/gpu_burn: gpu_burn.c
	mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

check: all
	./verify-boost.sh

diagnose:
	./mi50-diagnose.sh

clean:
	rm -rf $(BUILDDIR)

.PHONY: all check diagnose clean
