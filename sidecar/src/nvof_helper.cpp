// Process-isolated native Linux/Windows NVIDIA Optical Flow. No CUDA kernels.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#else
#include <dlfcn.h>
#endif
#include <nvOpticalFlowCuda.h>
#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
constexpr std::uint32_t magic = 0x31464f4e; // NOF1, little endian
static_assert(std::endian::native == std::endian::little);
static_assert(sizeof(NV_OF_FLOW_VECTOR) == 4);

void cu_check(CUresult code, const char* op) {
    if (code != CUDA_SUCCESS) throw std::runtime_error(std::string(op) + ": CUDA=" + std::to_string(code));
}
void of_check(NV_OF_STATUS code, const char* op) {
    if (code != NV_OF_SUCCESS) throw std::runtime_error(std::string(op) + ": NVOF=" + std::to_string(code));
}
void fatal_sync(CUresult code) noexcept {
    if (code != CUDA_SUCCESS) {
        std::fprintf(stderr, "GPU synchronization failed: CUDA=%d\n", static_cast<int>(code));
        std::_Exit(70); // Never unwind owners while GPU completion is unknown.
    }
}
struct Library {
#ifdef _WIN32
    HMODULE handle;
    explicit Library(const char* name) : handle(LoadLibraryExA(name, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32)) {
        if (!handle) throw std::runtime_error(std::string("load ") + name + ": Win32=" + std::to_string(GetLastError()));
    }
    ~Library() { if (handle) FreeLibrary(handle); }
#else
    void* handle;
    explicit Library(const char* name) : handle(dlopen(name, RTLD_NOW | RTLD_LOCAL)) {
        if (!handle) throw std::runtime_error(std::string("load ") + name + ": " + dlerror());
    }
    ~Library() { if (handle) dlclose(handle); }
#endif
    Library(const Library&) = delete;
    Library& operator=(const Library&) = delete;
    template<class T> T symbol(const char* name) const {
#ifdef _WIN32
        const auto address = GetProcAddress(handle, name);
        if (!address) throw std::runtime_error(std::string("missing export: ") + name);
        // Windows guarantees a GetProcAddress result can be converted to the
        // exact declared export type. T always comes from the vendor header.
        return reinterpret_cast<T>(address);
#else
        dlerror();
        void* address = dlsym(handle, name);
        if (const char* error = dlerror()) throw std::runtime_error(std::string(name) + ": " + error);
        if (!address) throw std::runtime_error(std::string("null symbol: ") + name);
        T result{};
        static_assert(sizeof(result) == sizeof(address));
        std::memcpy(&result, &address, sizeof(result)); // POSIX dlsym function address.
        return result;
#endif
    }
};
struct Cuda {
#ifdef _WIN32
    Library library{"nvcuda.dll"};
#else
    Library library{"libcuda.so.1"};
#endif
    decltype(&cuInit) init = library.symbol<decltype(init)>("cuInit");
    decltype(&cuDeviceGet) device_get = library.symbol<decltype(device_get)>("cuDeviceGet");
    decltype(&cuDeviceGetName) device_name = library.symbol<decltype(device_name)>("cuDeviceGetName");
    decltype(&cuDriverGetVersion) driver_version = library.symbol<decltype(driver_version)>("cuDriverGetVersion");
    decltype(&cuDevicePrimaryCtxRetain) retain = library.symbol<decltype(retain)>("cuDevicePrimaryCtxRetain");
    decltype(&cuDevicePrimaryCtxRelease) release = library.symbol<decltype(release)>("cuDevicePrimaryCtxRelease_v2");
    decltype(&cuCtxSetCurrent) current = library.symbol<decltype(current)>("cuCtxSetCurrent");
    decltype(&cuCtxSynchronize) sync = library.symbol<decltype(sync)>("cuCtxSynchronize");
    decltype(&cuMemcpy2D) copy = library.symbol<decltype(copy)>("cuMemcpy2D_v2");
};
struct Context {
    Cuda& cuda;
    CUdevice device{};
    CUcontext context{};
    Context(Cuda& api, int ordinal) : cuda(api) {
        cu_check(cuda.init(0), "cuInit");
        cu_check(cuda.device_get(&device, ordinal), "cuDeviceGet");
        cu_check(cuda.retain(&context, device), "cuDevicePrimaryCtxRetain");
        const auto status = cuda.current(context);
        if (status != CUDA_SUCCESS) {
            const auto released = cuda.release(device);
            if (released != CUDA_SUCCESS) std::fprintf(stderr, "context cleanup failed: %d\n", static_cast<int>(released));
            context = nullptr;
            cu_check(status, "cuCtxSetCurrent");
        }
    }
    ~Context() {
        if (context) {
            fatal_sync(cuda.sync());
            const auto code = cuda.release(device);
            if (code != CUDA_SUCCESS) std::fprintf(stderr, "cuDevicePrimaryCtxRelease: %d\n", static_cast<int>(code));
        }
    }
    Context(const Context&) = delete;
    Context& operator=(const Context&) = delete;
};
struct Session {
    NV_OF_CUDA_API_FUNCTION_LIST& api;
    NvOFHandle handle{};
    explicit Session(NV_OF_CUDA_API_FUNCTION_LIST& functions, CUcontext context) : api(functions) {
        of_check(api.nvCreateOpticalFlowCuda(context, &handle), "NvCreateOpticalFlowCuda");
    }
    ~Session() {
        if (handle) {
            const auto code = api.nvOFDestroy(handle);
            if (code != NV_OF_SUCCESS) std::fprintf(stderr, "NvOFDestroy: %d\n", static_cast<int>(code));
        }
    }
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    std::vector<std::uint32_t> caps(NV_OF_CAPS capability) const {
        std::uint32_t count = 0;
        of_check(api.nvOFGetCaps(handle, capability, nullptr, &count), "NvOFGetCaps(size)");
        if (count == 0 || count > 128) throw std::runtime_error("invalid capability count");
        std::vector<std::uint32_t> values(count);
        of_check(api.nvOFGetCaps(handle, capability, values.data(), &count), "NvOFGetCaps(values)");
        if (count == 0 || count > values.size()) throw std::runtime_error("capability count changed");
        values.resize(count);
        return values;
    }
    std::uint32_t scalar(NV_OF_CAPS capability) const {
        const auto values = caps(capability);
        if (values.size() != 1) throw std::runtime_error("expected scalar capability");
        return values.front();
    }
    void initialize(std::uint32_t w, std::uint32_t h, std::uint32_t grid, NV_OF_PERF_LEVEL preset) {
        const auto grids = caps(NV_OF_CAPS_SUPPORTED_OUTPUT_GRID_SIZES);
        if (std::find(grids.begin(), grids.end(), grid) == grids.end()) throw std::runtime_error("unsupported output grid");
        if (w < scalar(NV_OF_CAPS_WIDTH_MIN) || w > scalar(NV_OF_CAPS_WIDTH_MAX) ||
            h < scalar(NV_OF_CAPS_HEIGHT_MIN) || h > scalar(NV_OF_CAPS_HEIGHT_MAX))
            throw std::runtime_error("analysis dimensions outside NVOF capabilities; increase analysis scale");
        NV_OF_INIT_PARAMS params{};
        params.width = w; params.height = h;
        params.outGridSize = static_cast<NV_OF_OUTPUT_VECTOR_GRID_SIZE>(grid);
        params.mode = NV_OF_MODE_OPTICALFLOW; params.perfLevel = preset;
        of_check(api.nvOFInit(handle, &params), "NvOFInit");
    }
};
struct Buffer {
    Session& session;
    NvOFGPUBufferHandle handle{};
    Buffer(Session& owner, std::uint32_t w, std::uint32_t h, bool output) : session(owner) {
        NV_OF_BUFFER_DESCRIPTOR description{};
        description.width = w; description.height = h;
        description.bufferUsage = output ? NV_OF_BUFFER_USAGE_OUTPUT : NV_OF_BUFFER_USAGE_INPUT;
        description.bufferFormat = output ? NV_OF_BUFFER_FORMAT_SHORT2 : NV_OF_BUFFER_FORMAT_GRAYSCALE8;
        of_check(session.api.nvOFCreateGPUBufferCuda(session.handle, &description,
                 NV_OF_CUDA_BUFFER_TYPE_CUDEVICEPTR, &handle), "NvOFCreateGPUBufferCuda");
    }
    ~Buffer() {
        if (handle) {
            const auto code = session.api.nvOFDestroyGPUBufferCuda(handle);
            if (code != NV_OF_SUCCESS) std::fprintf(stderr, "NvOFDestroyGPUBufferCuda: %d\n", static_cast<int>(code));
        }
    }
    Buffer(const Buffer&) = delete;
    Buffer& operator=(const Buffer&) = delete;
    void transfer(Cuda& cuda, void* host, std::size_t row_bytes, std::uint32_t rows, bool download) {
        NV_OF_CUDA_BUFFER_STRIDE_INFO stride{};
        of_check(session.api.nvOFGPUBufferGetStrideInfo(handle, &stride), "NvOFGPUBufferGetStrideInfo");
        if (stride.numPlanes != 1 || stride.strideInfo[0].strideXInBytes < row_bytes)
            throw std::runtime_error("unexpected GPU buffer pitch");
        const auto pointer = session.api.nvOFGPUBufferGetCUdeviceptr(handle);
        if (!pointer) throw std::runtime_error("null GPU buffer pointer");
        CUDA_MEMCPY2D copy{};
        copy.WidthInBytes = row_bytes; copy.Height = rows;
        if (download) {
            copy.srcMemoryType = CU_MEMORYTYPE_DEVICE; copy.srcDevice = pointer;
            copy.srcPitch = stride.strideInfo[0].strideXInBytes;
            copy.dstMemoryType = CU_MEMORYTYPE_HOST; copy.dstHost = host; copy.dstPitch = row_bytes;
        } else {
            copy.srcMemoryType = CU_MEMORYTYPE_HOST; copy.srcHost = host; copy.srcPitch = row_bytes;
            copy.dstMemoryType = CU_MEMORYTYPE_DEVICE; copy.dstDevice = pointer;
            copy.dstPitch = stride.strideInfo[0].strideXInBytes;
        }
        cu_check(cuda.copy(&copy), "cuMemcpy2D");
    }
};
bool read_exact(void* destination, std::size_t size, bool eof_ok = false) {
    const auto count = std::fread(destination, 1, size, stdin);
    if (eof_ok && count == 0 && std::feof(stdin)) return false;
    if (count != size) throw std::runtime_error("truncated request");
    return true;
}
void write_exact(const void* source, std::size_t size) {
    if (std::fwrite(source, 1, size, stdout) != size) throw std::runtime_error("output write failed");
}
std::uint32_t number(const char* argument, std::uint32_t maximum) {
    std::uint32_t value{};
    const std::string_view text(argument);
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || value > maximum)
        throw std::runtime_error("invalid numeric argument");
    return value;
}
void print_probe(Cuda& cuda, Context& context, Session& session) {
    std::array<char, 256> name{};
    cu_check(cuda.device_name(name.data(), static_cast<int>(name.size()), context.device), "cuDeviceGetName");
    int version{};
    cu_check(cuda.driver_version(&version), "cuDriverGetVersion");
    std::string safe;
    for (const char c : name) {
        if (!c) break;
        if (c == '"' || c == '\\') safe += '\\';
        if (static_cast<unsigned char>(c) >= 32) safe += c;
    }
    const auto grids = session.caps(NV_OF_CAPS_SUPPORTED_OUTPUT_GRID_SIZES);
    std::printf("{\"protocol\":1,\"api_version\":%u,\"cuda_driver_api\":%d,\"gpu\":\"%s\",\"grids\":[",
                static_cast<unsigned>(NV_OF_API_VERSION), version, safe.c_str());
    for (std::size_t i = 0; i < grids.size(); ++i) std::printf("%s%u", i ? "," : "", grids[i]);
    std::printf("],\"width_min\":%u,\"height_min\":%u,\"width_max\":%u,\"height_max\":%u}\n",
        session.scalar(NV_OF_CAPS_WIDTH_MIN), session.scalar(NV_OF_CAPS_HEIGHT_MIN),
        session.scalar(NV_OF_CAPS_WIDTH_MAX), session.scalar(NV_OF_CAPS_HEIGHT_MAX));
    if (std::fflush(stdout)) throw std::runtime_error("probe output failed");
}
void evaluate(Cuda& cuda, Session& session, Buffer& input, Buffer& reference, Buffer& output,
              bool reset, void* result, std::uint32_t gw, std::uint32_t gh) {
    NV_OF_EXECUTE_INPUT_PARAMS in{};
    in.inputFrame = input.handle; in.referenceFrame = reference.handle;
    in.disableTemporalHints = reset ? NV_OF_TRUE : NV_OF_FALSE;
    NV_OF_EXECUTE_OUTPUT_PARAMS out{};
    out.outputBuffer = output.handle;
    const auto status = session.api.nvOFExecute(session.handle, &in, &out);
    fatal_sync(cuda.sync()); // Also synchronize a failed submission before unwinding.
    of_check(status, "NvOFExecute");
    output.transfer(cuda, result, static_cast<std::size_t>(gw) * 4, gh, true);
}
int run(int argc, char** argv) {
    const bool probe = argc == 3 && std::string_view(argv[1]) == "--probe";
    const bool serve = argc == 8 && std::string_view(argv[1]) == "--serve";
    if (!probe && !serve) throw std::runtime_error("usage: --probe DEVICE | --serve WIDTH HEIGHT PRESET GRID DEVICE TEMPORAL");
    const auto device = number(argv[probe ? 2 : 6], 63);
    const auto w = probe ? 0 : number(argv[2], 7680);
    const auto h = probe ? 0 : number(argv[3], 4320);
    const auto preset = probe ? 10 : number(argv[4], 20);
    const auto grid = probe ? 4 : number(argv[5], 4);
    const auto temporal = probe ? 0 : number(argv[7], 1);
    if (serve && (w < 64 || h < 64 || (grid != 1 && grid != 2 && grid != 4) ||
                  (preset != 5 && preset != 10 && preset != 20))) throw std::runtime_error("invalid serve parameters");
    Cuda cuda;
    Context context(cuda, static_cast<int>(device));
#ifdef _WIN32
    Library optical{"nvofapi64.dll"};
#else
    Library optical{"libnvidia-opticalflow.so.1"};
#endif
    NV_OF_CUDA_API_FUNCTION_LIST api{};
    const auto create = optical.symbol<decltype(&NvOFAPICreateInstanceCuda)>("NvOFAPICreateInstanceCuda");
    of_check(create(NV_OF_API_VERSION, &api), "NvOFAPICreateInstanceCuda");
    if (!api.nvCreateOpticalFlowCuda || !api.nvOFInit || !api.nvOFGetCaps || !api.nvOFExecute ||
        !api.nvOFCreateGPUBufferCuda || !api.nvOFGPUBufferGetStrideInfo || !api.nvOFGPUBufferGetCUdeviceptr ||
        !api.nvOFDestroyGPUBufferCuda || !api.nvOFDestroy) throw std::runtime_error("incomplete NVOF function table");
    Session backward(api, context.context);
    if (probe) { print_probe(cuda, context, backward); return 0; }
    // Separate histories: never contaminate backward temporal hints with forward diagnostics.
    Session forward(api, context.context);
    backward.initialize(w, h, grid, static_cast<NV_OF_PERF_LEVEL>(preset));
    forward.initialize(w, h, grid, static_cast<NV_OF_PERF_LEVEL>(preset));
    const auto gw = (w + grid - 1) / grid, gh = (h + grid - 1) / grid;
    Buffer bi(backward, w, h, false), br(backward, w, h, false), bo(backward, gw, gh, true);
    Buffer fi(forward, w, h, false), fr(forward, w, h, false), fo(forward, gw, gh, true);
    const std::size_t pixels = static_cast<std::size_t>(w) * h;
    std::vector<std::uint8_t> previous(pixels), current(pixels);
    std::vector<NV_OF_FLOW_VECTOR> bflow(static_cast<std::size_t>(gw) * gh), fflow(bflow.size());
    std::printf("{\"protocol\":1,\"grid_width\":%u,\"grid_height\":%u}\n", gw, gh);
    if (std::fflush(stdout)) throw std::runtime_error("ready output failed");
    for (std::uint32_t index = 0; index < 100000; ++index) {
        std::array<std::uint32_t, 4> request{};
        if (!read_exact(request.data(), sizeof(request), true)) return 0;
        if (request[0] != magic || request[1] != index || request[2] > 1 || request[3] != pixels)
            throw std::runtime_error("invalid frame request");
        read_exact(previous.data(), pixels); read_exact(current.data(), pixels);
        bi.transfer(cuda, current.data(), w, h, false); br.transfer(cuda, previous.data(), w, h, false);
        fi.transfer(cuda, previous.data(), w, h, false); fr.transfer(cuda, current.data(), w, h, false);
        const bool reset = request[2] != 0 || temporal == 0 || index == 0;
        evaluate(cuda, backward, bi, br, bo, reset, bflow.data(), gw, gh);
        evaluate(cuda, forward, fi, fr, fo, reset, fflow.data(), gw, gh);
        const std::array<std::uint32_t, 4> response{magic, index, gw, gh};
        write_exact(response.data(), sizeof(response));
        write_exact(bflow.data(), bflow.size() * sizeof(NV_OF_FLOW_VECTOR));
        write_exact(fflow.data(), fflow.size() * sizeof(NV_OF_FLOW_VECTOR));
        if (std::fflush(stdout)) throw std::runtime_error("frame output failed");
    }
    throw std::runtime_error("session frame limit reached");
}
} // namespace
int main(int argc, char** argv) {
#ifndef _WIN32
    std::signal(SIGPIPE, SIG_IGN);
#else
    // Binary frame transport must not translate LF/CRLF or treat 0x1a as EOF.
    if (_setmode(_fileno(stdin), _O_BINARY) == -1 || _setmode(_fileno(stdout), _O_BINARY) == -1) {
        std::fprintf(stderr, "NVOF: cannot set binary standard streams\n");
        return 1;
    }
#endif
    try { return run(argc, argv); }
    catch (const std::exception& error) { std::fprintf(stderr, "NVOF: %s\n", error.what()); return 1; }
}
