// Capability discovery is not inference acceptance. Uses public SL ABI only.
#include "win32_raii.hpp"
#include <d3d12.h>
#include <dxgi1_4.h>
#include <sl.h>
#include <sl_dlss.h>
#include <sl_dlss_d.h>
#include <sl_dlss_g.h>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>

namespace {
using namespace comfy_dlss;
class output final {
    unique_handle file_;
public:
    explicit output(const std::filesystem::path& path)
        : file_(CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr)) {
        if (!file_) throw std::runtime_error("Cannot create report");
    }
    template<class... A> void event(const char* format, A... args) {
        char line[2048]{};
        int size = 0;
        if constexpr (sizeof...(args) == 0) size = std::snprintf(line, sizeof(line), "%s", format);
        else size = std::snprintf(line, sizeof(line), format, args...);
        if (size < 0 || static_cast<std::size_t>(size) >= sizeof(line)) throw std::runtime_error("Report overflow");
        DWORD written = 0;
        if (!WriteFile(file_.get(), line, static_cast<DWORD>(size), &written, nullptr) || written != static_cast<DWORD>(size)
            || !FlushFileBuffers(file_.get())) throw std::runtime_error("Cannot write report");
    }
};
template<class T> T symbol(const unique_module& module, const char* name) {
    const auto raw = module.symbol(name);
    if (!raw) throw std::runtime_error("Required export missing");
    return reinterpret_cast<T>(raw);
}
int run(int argc, wchar_t** argv) {
    if (argc != 4) return 2; // absolute runtime, NEW job, fg|rr|sr
    const std::filesystem::path runtime{argv[1]}, job{argv[2]};
    const std::wstring mode{argv[3]};
    if (!runtime.is_absolute() || !job.is_absolute() || (mode != L"fg" && mode != L"rr" && mode != L"sr")) return 2;
    if (!std::filesystem::create_directory(job)) return 2;
    output report{job / "capabilities.jsonl"};
    const auto logs = job / "logs"; std::filesystem::create_directory(logs);
    const char* label = mode == L"fg" ? "fg" : mode == L"rr" ? "rr" : "sr";
    report.event("{\"event\":\"begin\",\"feature\":\"%s\",\"inference_tested\":false}\n", label);
    if (mode == L"fg") {
        // A known execution requirement, not a focus measurement by this query.
        report.event("{\"event\":\"execution_notice\",\"code\":\"FG_FOREGROUND_REQUIRED\","
            "\"scope\":\"tested_present_capture\",\"foreground_required\":true,\"focus_checked\":false,"
            "\"message\":\"The currently tested FG Present-capture path requires the Worker window on the GPU host "
            "to remain foreground and focused, not the ComfyUI browser. Background FG is not verified. "
            "eOff disables generation; eRetainResourcesWhenOff only retains resources while disabled. "
            "This capability query does not create or focus an FG window.\"}\n");
    }
    unique_module interposer{LoadLibraryExW((runtime / "sl.interposer.dll").c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)};
    unique_module dxgi, d3d;
    com_ptr<IDXGIFactory1> factory;
    com_ptr<ID3D12Device> device;
    bool initialized = false;
    PFun_slShutdown* shutdown = nullptr;
    try {
        const auto init = symbol<PFun_slInit*>(interposer, "slInit");
        shutdown = symbol<PFun_slShutdown*>(interposer, "slShutdown");
        const auto supported = symbol<PFun_slIsFeatureSupported*>(interposer, "slIsFeatureSupported");
        const auto requirements = symbol<PFun_slGetFeatureRequirements*>(interposer, "slGetFeatureRequirements");
        const auto version = symbol<PFun_slGetFeatureVersion*>(interposer, "slGetFeatureVersion");
        const auto set_device = symbol<PFun_slSetD3DDevice*>(interposer, "slSetD3DDevice");
        const auto get_function = symbol<PFun_slGetFeatureFunction*>(interposer, "slGetFeatureFunction");
        const sl::Feature feature = mode == L"fg" ? sl::kFeatureDLSS_G : mode == L"rr" ? sl::kFeatureDLSS_RR : sl::kFeatureDLSS;
        const sl::Feature features[]{feature, sl::kFeatureReflex, sl::kFeaturePCL};
        const auto path = runtime.wstring(); const wchar_t* paths[]{path.c_str()};
        sl::Preferences prefs{};
        prefs.pathsToPlugins = paths; prefs.numPathsToPlugins = 1; prefs.pathToLogsAndData = logs.c_str();
        prefs.featuresToLoad = features; prefs.numFeaturesToLoad = mode == L"fg" ? 3U : 1U;
        prefs.engine = sl::EngineType::eCustom; prefs.engineVersion = "comfy-feature-capability-1";
        prefs.projectId = "1db01747-a936-47d6-80fd-ffe14ce8aab8";
        prefs.renderAPI = sl::RenderAPI::eD3D12; prefs.logLevel = sl::LogLevel::eVerbose;
        prefs.flags = sl::PreferenceFlags::eUseManualHooking | sl::PreferenceFlags::eDisableCLStateTracking |
            sl::PreferenceFlags::eUseFrameBasedResourceTagging | sl::PreferenceFlags::eUseDXGIFactoryProxy;
        auto result = init(prefs, sl::kSDKVersion);
        report.event("{\"event\":\"slInit\",\"result\":%u}\n", static_cast<unsigned int>(result));
        if (result != sl::Result::eOk) throw std::runtime_error("slInit failed");
        initialized = true;
        sl::FeatureRequirements req{}; result = requirements(feature, req);
        report.event("{\"event\":\"requirements\",\"result\":%u,\"flags\":%u,\"tags\":%u}\n",
            static_cast<unsigned int>(result), static_cast<unsigned int>(req.flags), req.numRequiredTags);
        sl::FeatureVersion ver{}; result = version(feature, ver);
        report.event("{\"event\":\"version\",\"result\":%u,\"sl\":[%u,%u,%u],\"ngx\":[%u,%u,%u]}\n",
            static_cast<unsigned int>(result), ver.versionSL.major, ver.versionSL.minor, ver.versionSL.build,
            ver.versionNGX.major, ver.versionNGX.minor, ver.versionNGX.build);
        dxgi.reset(LoadLibraryExW(L"dxgi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        d3d.reset(LoadLibraryExW(L"d3d12.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        const auto create_factory = symbol<decltype(&CreateDXGIFactory1)>(dxgi, "CreateDXGIFactory1");
        const auto create_device = symbol<PFN_D3D12_CREATE_DEVICE>(d3d, "D3D12CreateDevice");
        void* raw = nullptr;
        auto hr = create_factory(IID_IDXGIFactory1, &raw); factory.reset(static_cast<IDXGIFactory1*>(raw));
        if (FAILED(hr) || !factory) throw std::runtime_error("Factory unavailable");
        bool found = false;
        for (UINT index = 0; ; ++index) {
            IDXGIAdapter1* pointer = nullptr; hr = factory->EnumAdapters1(index, &pointer);
            com_ptr<IDXGIAdapter1> adapter{pointer};
            if (hr == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(hr) || !adapter) throw std::runtime_error("Adapter enumeration failed");
            DXGI_ADAPTER_DESC1 desc{};
            if (FAILED(adapter->GetDesc1(&desc))) throw std::runtime_error("Adapter description failed");
            if (desc.VendorId != 0x10de || (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
            sl::AdapterInfo info{}; info.deviceLUID = reinterpret_cast<unsigned char*>(&desc.AdapterLuid);
            info.deviceLUIDSizeInBytes = sizeof(desc.AdapterLuid);
            result = supported(feature, info); found = true;
            report.event("{\"event\":\"feature_support\",\"result\":%u,\"vendor\":%u,\"device\":%u}\n",
                static_cast<unsigned int>(result), desc.VendorId, desc.DeviceId);
            if (result != sl::Result::eOk) break; // Capability rejection is data, never overridden.
            raw = nullptr; hr = create_device(adapter.get(), D3D_FEATURE_LEVEL_12_0, IID_ID3D12Device, &raw);
            device.reset(static_cast<ID3D12Device*>(raw));
            if (FAILED(hr) || !device) throw std::runtime_error("Device unavailable");
            result = set_device(device.get());
            report.event("{\"event\":\"slSetD3DDevice\",\"result\":%u}\n", static_cast<unsigned int>(result));
            if (result != sl::Result::eOk) throw std::runtime_error("Set device failed");
            raw = nullptr;
            const char* entry = mode == L"fg" ? "slDLSSGGetState" : mode == L"rr" ? "slDLSSDGetOptimalSettings" : "slDLSSGetOptimalSettings";
            result = get_function(feature, entry, raw);
            if (result != sl::Result::eOk || !raw) throw std::runtime_error("Feature entry missing");
            if (mode == L"fg") {
                sl::DLSSGState state{};
                result = reinterpret_cast<PFun_slDLSSGGetState*>(raw)(sl::ViewportHandle{0}, state, nullptr);
                report.event("{\"event\":\"fg_state\",\"result\":%u,\"status\":%u,\"max_generated_frames\":%u,\"minimum_dimension\":%u,\"dynamic_mfg\":%u}\n",
                    static_cast<unsigned int>(result), static_cast<unsigned int>(state.status), state.numFramesToGenerateMax,
                    state.minWidthOrHeight, static_cast<unsigned int>(state.bIsDynamicMFGSupported));
            } else if (mode == L"rr") {
                sl::DLSSDOptions options{}; options.mode = sl::DLSSMode::eDLAA;
                options.outputWidth = 960; options.outputHeight = 540;
                sl::DLSSDOptimalSettings settings{};
                result = reinterpret_cast<PFun_slDLSSDGetOptimalSettings*>(raw)(options, settings);
                report.event("{\"event\":\"rr_native_resolution_query\",\"result\":%u,\"width\":%u,\"height\":%u}\n",
                    static_cast<unsigned int>(result), settings.optimalRenderWidth, settings.optimalRenderHeight);
            } else {
                sl::DLSSOptions options{}; options.mode = sl::DLSSMode::eDLAA;
                options.outputWidth = 960; options.outputHeight = 540;
                sl::DLSSOptimalSettings settings{};
                result = reinterpret_cast<PFun_slDLSSGetOptimalSettings*>(raw)(options, settings);
                report.event("{\"event\":\"dlaa_query\",\"result\":%u,\"width\":%u,\"height\":%u}\n",
                    static_cast<unsigned int>(result), settings.optimalRenderWidth, settings.optimalRenderHeight);
            }
            if (result != sl::Result::eOk) throw std::runtime_error("Feature query failed");
            break;
        }
        if (!found) throw std::runtime_error("No NVIDIA adapter");
        result = shutdown(); initialized = false;
        report.event("{\"event\":\"slShutdown\",\"result\":%u}\n", static_cast<unsigned int>(result));
        if (result != sl::Result::eOk) throw std::runtime_error("Shutdown failed");
        device.reset(); factory.reset(); interposer.reset();
        report.event("{\"event\":\"complete\",\"ok\":true,\"inference_tested\":false}\n");
        return 0;
    } catch (const std::exception& error) {
        // Fixed local exception messages contain no arbitrary JSON characters.
        report.event("{\"event\":\"error\",\"message\":\"%s\"}\n", error.what());
        if (initialized && shutdown) {
            const auto result = shutdown();
            report.event("{\"event\":\"slShutdown\",\"result\":%u}\n", static_cast<unsigned int>(result));
        }
        device.reset(); factory.reset(); interposer.reset();
        return 1;
    }
}
}
int wmain(int argc, wchar_t** argv) { try { return run(argc, argv); } catch (...) { return 2; } }
