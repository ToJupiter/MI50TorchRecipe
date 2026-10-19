/*
 * gpu_burn.c — self-contained OpenCL FP32 FMA burner for MI50 / Vega20.
 *
 * Build:  gcc -O2 -o gpu_burn gpu_burn.c -lOpenCL
 * Usage:  ./gpu_burn [seconds=30] [iters_per_launch=30000] [threads=122880]
 *
 * Notes:
 *  - Picks the first GPU with >=60 CUs or a known Vega/gfx906 name, so it
 *    targets the MI50 and not the iGPU.
 *  - Launches are synchronous (one clFinish per launch). Do NOT remove that:
 *    an async loop with a single final clFinish queues thousands of kernels
 *    and looks exactly like a GPU hang.
 *  - On ROCm 7.x, gfx906 needs HSA_OVERRIDE_GFX_VERSION=9.0.0 in the env;
 *    the program then reports gfx900 but still runs on the MI50.
 */
#define CL_TARGET_OPENCL_VERSION 120
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <CL/cl.h>

static const char *src =
"__kernel void burn(__global float *out, const int iters, const float seed) {\n"
"  float a = (float)get_global_id(0) * 1e-7f + seed;\n"
"  float b = a * 1.000001f;\n"
"  float c = a + b;\n"
"  for (int i = 0; i < iters; i++) {\n"
"    a = fma(a, 1.0000001f, c);\n"
"    b = fma(b, 1.0000001f, a);\n"
"    c = fma(c, 1.0000001f, b);\n"
"    a = fma(a, 1.0000001f, c);\n"
"    b = fma(b, 1.0000001f, a);\n"
"    c = fma(c, 1.0000001f, b);\n"
"    a = fma(a, 1.0000001f, c);\n"
"    b = fma(b, 1.0000001f, a);\n"
"  }\n"
"  out[get_global_id(0)] = a + b + c;\n"
"}\n";

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	int secs = argc > 1 ? atoi(argv[1]) : 30;
	int iters = argc > 2 ? atoi(argv[2]) : 30000;
	int nthreads = argc > 3 ? atoi(argv[3]) : 122880;
	cl_platform_id p;
	cl_device_id dev = NULL;
	cl_uint np = 0, nd = 0;
	cl_int err;
	char name[256] = "?";
	cl_uint maxcu = 0;

	err = clGetPlatformIDs(0, NULL, &np);
	if (err != CL_SUCCESS || np == 0) { fprintf(stderr, "no platforms\n"); return 1; }
	cl_platform_id *plats = calloc(np, sizeof(*plats));
	clGetPlatformIDs(np, plats, NULL);
	for (cl_uint i = 0; i < np && !dev; i++) {
		p = plats[i];
		if (clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, 0, NULL, &nd) != CL_SUCCESS || nd == 0)
			continue;
		cl_device_id *devs = calloc(nd, sizeof(*devs));
		clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, nd, devs, NULL);
		for (cl_uint j = 0; j < nd; j++) {
			clGetDeviceInfo(devs[j], CL_DEVICE_NAME, sizeof(name), name, NULL);
			clGetDeviceInfo(devs[j], CL_DEVICE_MAX_COMPUTE_UNITS, sizeof(maxcu), &maxcu, NULL);
			if (strstr(name, "gfx906") || strstr(name, "Vega") ||
			    strstr(name, "MI50") || strstr(name, "Pro VII") ||
			    strstr(name, "Radeon") || maxcu >= 60) {
				dev = devs[j];
				break;
			}
		}
		free(devs);
	}
	if (!dev) { fprintf(stderr, "no suitable GPU device\n"); return 1; }
	printf("device: %s (%u CUs), %d threads, %d iters/launch, %d s\n",
	       name, maxcu, nthreads, iters, secs);

	cl_context ctx = clCreateContext(NULL, 1, &dev, NULL, NULL, &err);
	if (!ctx) { fprintf(stderr, "context err %d\n", err); return 1; }
	cl_command_queue q = clCreateCommandQueue(ctx, dev, 0, &err);
	cl_program prog = clCreateProgramWithSource(ctx, 1, &src, NULL, &err);
	err = clBuildProgram(prog, 1, &dev, "-cl-mad-enable", NULL, NULL);
	if (err != CL_SUCCESS) {
		size_t n = 0;
		clGetProgramBuildInfo(prog, dev, CL_PROGRAM_BUILD_LOG, 0, NULL, &n);
		char *log = malloc(n + 1);
		clGetProgramBuildInfo(prog, dev, CL_PROGRAM_BUILD_LOG, n, log, NULL);
		fprintf(stderr, "build failed:\n%s\n", log);
		return 1;
	}
	cl_kernel k = clCreateKernel(prog, "burn", &err);
	cl_mem buf = clCreateBuffer(ctx, CL_MEM_WRITE_ONLY, (size_t)nthreads * 4, NULL, &err);
	size_t zero = 0; float seed = 0.123f;
	clSetKernelArg(k, 0, sizeof(buf), &buf);
	clSetKernelArg(k, 1, sizeof(iters), &iters);
	clSetKernelArg(k, 2, sizeof(seed), &seed);

	size_t gws = nthreads, lws = 256;
	clEnqueueNDRangeKernel(q, k, 1, &zero, &gws, &lws, 0, NULL, NULL);
	clFinish(q); /* warmup */

	double t0 = now(), elapsed = 0;
	long runs = 0;
	printf("running...\n");
	fflush(stdout);
	do {
		clEnqueueNDRangeKernel(q, k, 1, &zero, &gws, &lws, 0, NULL, NULL);
		clFinish(q);
		runs++;
		elapsed = now() - t0;
	} while (elapsed < secs);
	elapsed = now() - t0;

	double flops = (double)nthreads * iters * 8.0 * 2.0 * runs;
	printf("achieved %.2f GFLOP/s fp32 (%.2f s, %ld launches)\n",
	       flops / elapsed / 1e9, elapsed, runs);
	return 0;
}
