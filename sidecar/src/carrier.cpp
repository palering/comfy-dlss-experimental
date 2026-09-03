#include <d3d12.h>
#include <dxgi1_4.h>

#include "frame_worker.hpp"
#include "win32_raii.hpp"
#include "ngx_smoke.hpp"

#include <fcntl.h>
#include <io.h>
#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cstdio>
#include <cwchar>
#include <filesystem>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

namespace {

namespace fs = std::filesystem;

constexpr wchar_t kWindowClassName[] = L"ComfyDlssExperimentalCarrierWindow";

struct CarrierResult final {
    bool runtime_files_complete = false;
    bool reshade_loaded = false;
    bool reshade_log_present = false;
    bool reshade_log_mentions_renodx = false;
    bool renodx_loaded = false;
    bool adapter_selected = false;
    bool d3d12_created = false;
    bool window_created = false;
    bool swapchain_created = false;
    bool wine_x11_driver_loaded = false;
    bool wine_wayland_driver_loaded = false;
    bool frame_worker_requested = false;
    bool frame_worker_completed = false;
    unsigned int presents_requested = 120;
    unsigned int presents_completed = 0;
    unsigned int adapter_vendor_id = 0;
    unsigned int adapter_device_id = 0;
    HRESULT last_present_result = S_OK;
    std::string stage = "arguments";
    std::string error;
    std::string executable_directory;
    std::string reshade_module_path;
    std::string adapter_name;
    comfy_dlss::ngx_smoke_result ngx;
};

struct FileCloser final {
    void operator()(FILE* file) const noexcept {
        if (file) std::fclose(file);
    }
};

std::string json_escape(std::string_view value) {
    std::string escaped;
    escaped.reserve(value.size() + 8);
    for (const char raw_character : value) {
        const auto character = static_cast<unsigned char>(raw_character);
        switch (character) {
            case '\\': escaped += "\\\\"; break;
            case '"': escaped += "\\\""; break;
            case '\n': escaped += "\\n"; break;
            case '\r': escaped += "\\r"; break;
            case '\t': escaped += "\\t"; break;
            default:
                if (character < 0x20) {
                    char encoded[7]{};
                    std::snprintf(encoded, sizeof(encoded), "\\u%04x", character);
                    escaped += encoded;
                } else {
                    escaped += static_cast<char>(character);
                }
        }
    }
    return escaped;
}

std::string hex_code(unsigned long code) {
    char value[16]{};
    std::snprintf(value, sizeof(value), "0x%08lx", code);
    return value;
}

std::string win32_message(DWORD code) {
    std::array<char, 1024> buffer{};
    const DWORD length = FormatMessageA(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        code,
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        buffer.data(),
        static_cast<DWORD>(buffer.size()),
        nullptr);
    std::string message = length ? std::string(buffer.data(), length) : "unknown Win32 error";
    while (!message.empty() && (message.back() == '\r' || message.back() == '\n')) {
        message.pop_back();
    }
    return message;
}

std::string wide_to_utf8(std::wstring_view value) {
    if (value.empty()) return {};
    if (value.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) return {};
    const int source_length = static_cast<int>(value.size());
    const int required = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), source_length, nullptr, 0, nullptr, nullptr);
    if (required <= 0) return {};
    std::string output(static_cast<std::size_t>(required), '\0');
    const int converted = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), source_length,
        output.data(), required, nullptr, nullptr);
    return converted == required ? output : std::string{};
}

std::wstring module_path(HMODULE module) {
    std::wstring path(512, L'\0');
    while (path.size() <= 32768) {
        const DWORD length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
        if (length == 0) return {};
        if (length < path.size() - 1) {
            path.resize(length);
            return path;
        }
        path.resize(path.size() * 2);
    }
    return {};
}

bool file_exists(const fs::path& path) {
    std::error_code error;
    return fs::is_regular_file(path, error);
}

bool file_contains_case_insensitive(const fs::path& path, std::string_view needle) {
    std::unique_ptr<FILE, FileCloser> input{_wfopen(path.c_str(), L"rb")};
    if (!input) return false;
    if (std::fseek(input.get(), 0, SEEK_END) != 0) return false;
    const long length = std::ftell(input.get());
    if (length < 0 || length > 8L * 1024L * 1024L) return false;
    if (std::fseek(input.get(), 0, SEEK_SET) != 0) return false;
    std::string contents(static_cast<std::size_t>(length), '\0');
    if (!contents.empty() && std::fread(contents.data(), 1, contents.size(), input.get()) != contents.size()) {
        return false;
    }
    std::transform(contents.begin(), contents.end(), contents.begin(), [](unsigned char character) {
        return static_cast<char>(std::tolower(character));
    });
    std::string lowered_needle(needle);
    std::transform(lowered_needle.begin(), lowered_needle.end(), lowered_needle.begin(), [](unsigned char character) {
        return static_cast<char>(std::tolower(character));
    });
    return contents.find(lowered_needle) != std::string::npos;
}

bool set_default_ini_value(
    const fs::path& ini_path,
    const wchar_t* section,
    const wchar_t* key,
    const wchar_t* value,
    std::string& error) {
    std::array<wchar_t, 64> current{};
    GetPrivateProfileStringW(section, key, L"", current.data(), static_cast<DWORD>(current.size()), ini_path.c_str());
    if (current[0] != L'\0') return true;
    if (WritePrivateProfileStringW(section, key, value, ini_path.c_str()) == FALSE) {
        const DWORD code = GetLastError();
        error = "WritePrivateProfileStringW(" + wide_to_utf8(key) + ") failed: " +
            hex_code(code) + " " + win32_message(code);
        return false;
    }
    return true;
}

bool prepare_reshade_ini(const fs::path& directory, std::string& error) {
    const fs::path ini_path = directory / L"ReShade.ini";
    const std::array<std::array<const wchar_t*, 3>, 7> defaults = {{
        {L"GENERAL", L"EffectSearchPaths", L".\\"},
        {L"GENERAL", L"TextureSearchPaths", L".\\"},
        {L"RenoDX.DLSS5", L"EnableHooks", L"2"},
        {L"RenoDX.DLSS5", L"NeuralUplift", L"1"},
        {L"RenoDX.DLSS5", L"NREnableUpscaling", L"0"},
        {L"RenoDX.DLSS5", L"NRToggleKey", L"0"},
        {L"RenoDX.DLSS5", L"NRScreenshotKey", L"0"},
    }};
    for (const auto& entry : defaults) {
        if (!set_default_ini_value(ini_path, entry[0], entry[1], entry[2], error)) return false;
    }
    return true;
}

bool parse_unsigned(std::string_view text, unsigned int minimum, unsigned int maximum, unsigned int& output) {
    unsigned int parsed = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) return false;
    if (parsed < minimum || parsed > maximum) return false;
    output = parsed;
    return true;
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) {
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

void write_result(FILE* output, const CarrierResult& result) {
    const bool bootstrap_ok =
        result.runtime_files_complete && result.reshade_loaded && result.renodx_loaded &&
        result.adapter_selected && result.d3d12_created && result.window_created && result.swapchain_created &&
        result.presents_completed == result.presents_requested && SUCCEEDED(result.last_present_result) &&
        (!result.ngx.requested || result.ngx.ok()) &&
        (!result.frame_worker_requested || result.frame_worker_completed);
    const std::string error_json = result.error.empty()
        ? "null"
        : "\"" + json_escape(result.error) + "\"";
    std::fprintf(output, "{");
    std::fprintf(output, "\"schema_version\":1,");
    std::fprintf(output, "\"carrier\":\"d3d12-reshade-renodx-bootstrap\",");
    std::fprintf(output, "\"ok\":%s,", bootstrap_ok ? "true" : "false");
    std::fprintf(output, "\"dlss_evaluation_implemented\":false,");
    std::fprintf(output, "\"stage\":\"%s\",", json_escape(result.stage).c_str());
    std::fprintf(output, "\"error\":%s,", error_json.c_str());
    std::fprintf(output, "\"executable_directory\":\"%s\",", json_escape(result.executable_directory).c_str());
    std::fprintf(output, "\"runtime_files_complete\":%s,", result.runtime_files_complete ? "true" : "false");
    std::fprintf(output, "\"reshade\":{");
    std::fprintf(output, "\"loaded\":%s,", result.reshade_loaded ? "true" : "false");
    std::fprintf(output, "\"module_path\":\"%s\",", json_escape(result.reshade_module_path).c_str());
    std::fprintf(output, "\"log_present\":%s,", result.reshade_log_present ? "true" : "false");
    std::fprintf(output, "\"log_mentions_renodx\":%s},", result.reshade_log_mentions_renodx ? "true" : "false");
    std::fprintf(output, "\"renodx\":{\"module_loaded\":%s},", result.renodx_loaded ? "true" : "false");
    std::fprintf(output, "\"adapter\":{");
    std::fprintf(output, "\"selected\":%s,", result.adapter_selected ? "true" : "false");
    std::fprintf(output, "\"name\":\"%s\",", json_escape(result.adapter_name).c_str());
    std::fprintf(output, "\"vendor_id\":%u,", result.adapter_vendor_id);
    std::fprintf(output, "\"device_id\":%u},", result.adapter_device_id);
    std::fprintf(output, "\"d3d12\":{\"created\":%s,\"requested_feature_level\":\"12_0\"},",
        result.d3d12_created ? "true" : "false");
    std::fprintf(output, "\"window_created\":%s,", result.window_created ? "true" : "false");
    std::fprintf(output, "\"swapchain_created\":%s,", result.swapchain_created ? "true" : "false");
    std::fprintf(output, "\"frame_worker\":{\"requested\":%s,\"completed\":%s},",
        result.frame_worker_requested ? "true" : "false",
        result.frame_worker_completed ? "true" : "false");
    std::fprintf(output, "\"wine_graphics_driver\":{");
    std::fprintf(output, "\"winex11_loaded\":%s,", result.wine_x11_driver_loaded ? "true" : "false");
    std::fprintf(output, "\"winewayland_loaded\":%s},", result.wine_wayland_driver_loaded ? "true" : "false");
    std::fprintf(output, "\"ngx_synthetic_smoke\":{");
    std::fprintf(output, "\"requested\":%s,", result.ngx.requested ? "true" : "false");
    std::fprintf(output, "\"ok\":%s,", result.ngx.ok() ? "true" : "false");
    std::fprintf(output, "\"exports_resolved\":%s,", result.ngx.exports_resolved ? "true" : "false");
    std::fprintf(output, "\"initialized\":%s,", result.ngx.initialized ? "true" : "false");
    std::fprintf(output, "\"parameters_allocated\":%s,", result.ngx.parameters_allocated ? "true" : "false");
    std::fprintf(output, "\"feature_created\":%s,", result.ngx.feature_created ? "true" : "false");
    std::fprintf(output, "\"init_result\":\"%s\",", hex_code(result.ngx.init_result).c_str());
    std::fprintf(output, "\"create_result\":\"%s\",", hex_code(result.ngx.create_result).c_str());
    std::fprintf(output, "\"last_evaluate_result\":\"%s\",", hex_code(result.ngx.last_evaluate_result).c_str());
    std::fprintf(output, "\"evaluations_requested\":%u,", result.ngx.evaluations_requested);
    std::fprintf(output, "\"evaluations_completed\":%u,", result.ngx.evaluations_completed);
    const std::string ngx_error_json = result.ngx.error.empty()
        ? "null"
        : "\"" + json_escape(result.ngx.error) + "\"";
    std::fprintf(output, "\"error\":%s},", ngx_error_json.c_str());
    std::fprintf(output, "\"present\":{\"requested\":%u,\"completed\":%u,\"last_hresult\":\"%s\"}}\n",
        result.presents_requested,
        result.presents_completed,
        hex_code(static_cast<unsigned long>(result.last_present_result)).c_str());
}

int run_carrier(
    unsigned int interval_ms,
    bool run_ngx,
    bool run_worker,
    const char* worker_tcp_host,
    std::uint16_t worker_tcp_port,
    std::string_view worker_tcp_token,
    unsigned int ngx_evaluations,
    CarrierResult& result) {
    const std::wstring executable_path = module_path(nullptr);
    if (executable_path.empty()) {
        const DWORD code = GetLastError();
        result.error = "GetModuleFileNameW failed: " + hex_code(code) + " " + win32_message(code);
        return 10;
    }
    const fs::path directory = fs::path(executable_path).parent_path();
    result.executable_directory = wide_to_utf8(directory.native());
    if (SetCurrentDirectoryW(directory.c_str()) == FALSE) {
        const DWORD code = GetLastError();
        result.error = "SetCurrentDirectoryW failed: " + hex_code(code) + " " + win32_message(code);
        return 11;
    }

    const fs::path reshade_path = directory / L"dxgi.dll";
    const fs::path renodx_path = directory / L"renodx-dlss5.addon64";
    const fs::path dlss_path = directory / L"nvngx_dlss.dll";
    const fs::path dlssnr_path = directory / L"nvngx_dlssnr.dll";
    result.runtime_files_complete =
        file_exists(reshade_path) && file_exists(renodx_path) &&
        file_exists(dlss_path) && file_exists(dlssnr_path);
    if (!result.runtime_files_complete) {
        result.stage = "runtime-files";
        result.error = "job directory must contain dxgi.dll, renodx-dlss5.addon64, nvngx_dlss.dll, and nvngx_dlssnr.dll";
        return 12;
    }
    if (!prepare_reshade_ini(directory, result.error)) {
        result.stage = "reshade-config";
        return 13;
    }

    result.stage = "reshade-load";
    comfy_dlss::unique_module reshade_module{LoadLibraryW(reshade_path.c_str())};
    if (!reshade_module) {
        const DWORD code = GetLastError();
        result.error = "LoadLibraryW(dxgi.dll) failed: " + hex_code(code) + " " + win32_message(code);
        return 20;
    }
    result.reshade_loaded = true;
    result.reshade_module_path = wide_to_utf8(module_path(reshade_module.get()));

    const auto create_factory = reinterpret_cast<decltype(&CreateDXGIFactory1)>(
        reshade_module.symbol("CreateDXGIFactory1"));
    if (!create_factory) {
        result.error = "ReShade dxgi.dll does not export CreateDXGIFactory1";
        return 21;
    }

    result.stage = "d3d12-load";
    comfy_dlss::unique_module d3d12_module{LoadLibraryW(L"d3d12.dll")};
    if (!d3d12_module) {
        const DWORD code = GetLastError();
        result.error = "LoadLibraryW(d3d12.dll) failed: " + hex_code(code) + " " + win32_message(code);
        return 22;
    }
    const auto create_device = reinterpret_cast<PFN_D3D12_CREATE_DEVICE>(
        d3d12_module.symbol("D3D12CreateDevice"));
    if (!create_device) {
        result.error = "d3d12.dll does not export D3D12CreateDevice";
        return 23;
    }

    result.stage = "dxgi-factory";
    void* raw_factory = nullptr;
    HRESULT hr = create_factory(IID_IDXGIFactory4, &raw_factory);
    comfy_dlss::com_ptr<IDXGIFactory4> factory{static_cast<IDXGIFactory4*>(raw_factory)};
    if (FAILED(hr) || !factory) {
        result.error = "CreateDXGIFactory1 failed: " + hex_code(static_cast<unsigned long>(hr));
        return 32;
    }

    result.stage = "dxgi-adapter";
    comfy_dlss::com_ptr<IDXGIAdapter1> adapter;
    DXGI_ADAPTER_DESC1 adapter_desc{};
    for (UINT index = 0;; ++index) {
        IDXGIAdapter1* raw_adapter = nullptr;
        hr = factory->EnumAdapters1(index, &raw_adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(hr)) {
            result.error = "IDXGIFactory4::EnumAdapters1 failed: " +
                hex_code(static_cast<unsigned long>(hr));
            return 33;
        }
        comfy_dlss::com_ptr<IDXGIAdapter1> candidate{raw_adapter};
        DXGI_ADAPTER_DESC1 candidate_desc{};
        hr = candidate->GetDesc1(&candidate_desc);
        if (FAILED(hr)) continue;
        if ((candidate_desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0) continue;
        if (candidate_desc.VendorId != 0x10deU) continue;
        adapter = std::move(candidate);
        adapter_desc = candidate_desc;
        break;
    }
    if (!adapter) {
        result.error = "No hardware NVIDIA DXGI adapter was found";
        return 34;
    }
    result.adapter_selected = true;
    result.adapter_vendor_id = adapter_desc.VendorId;
    result.adapter_device_id = adapter_desc.DeviceId;
    constexpr std::size_t description_capacity =
        sizeof(adapter_desc.Description) / sizeof(adapter_desc.Description[0]);
    std::size_t description_length = 0;
    while (description_length < description_capacity &&
           adapter_desc.Description[description_length] != L'\0') {
        ++description_length;
    }
    result.adapter_name = wide_to_utf8(
        std::wstring_view(adapter_desc.Description, description_length));

    result.stage = "d3d12-device";
    void* raw_device = nullptr;
    hr = create_device(adapter.get(), D3D_FEATURE_LEVEL_12_0, IID_ID3D12Device, &raw_device);
    comfy_dlss::com_ptr<ID3D12Device> device{static_cast<ID3D12Device*>(raw_device)};
    if (FAILED(hr) || !device) {
        result.error = "D3D12CreateDevice failed: " + hex_code(static_cast<unsigned long>(hr));
        return 30;
    }
    result.d3d12_created = true;

    D3D12_COMMAND_QUEUE_DESC queue_desc{};
    queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    queue_desc.Priority = D3D12_COMMAND_QUEUE_PRIORITY_NORMAL;
    queue_desc.Flags = D3D12_COMMAND_QUEUE_FLAG_NONE;
    void* raw_queue = nullptr;
    hr = device->CreateCommandQueue(&queue_desc, IID_ID3D12CommandQueue, &raw_queue);
    comfy_dlss::com_ptr<ID3D12CommandQueue> queue{static_cast<ID3D12CommandQueue*>(raw_queue)};
    if (FAILED(hr) || !queue) {
        result.error = "ID3D12Device::CreateCommandQueue failed: " + hex_code(static_cast<unsigned long>(hr));
        return 31;
    }

    result.stage = "window";
    const HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = kWindowClassName;
    const ATOM class_atom = RegisterClassExW(&window_class);
    comfy_dlss::unique_window_class registered_class{instance, class_atom, kWindowClassName};
    if (!registered_class) {
        const DWORD code = GetLastError();
        result.error = "RegisterClassExW failed: " + hex_code(code) + " " + win32_message(code);
        return 40;
    }
    comfy_dlss::unique_window window{CreateWindowExW(
        0,
        kWindowClassName,
        L"Comfy DLSS Experimental Carrier",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        64,
        64,
        nullptr,
        nullptr,
        instance,
        nullptr)};
    if (!window) {
        const DWORD code = GetLastError();
        result.error = "CreateWindowExW failed (a graphical Wine/Proton session is required): " +
            hex_code(code) + " " + win32_message(code);
        return 41;
    }
    result.window_created = true;

    result.stage = "swapchain";
    DXGI_SWAP_CHAIN_DESC1 swapchain_desc{};
    swapchain_desc.Width = 64;
    swapchain_desc.Height = 64;
    swapchain_desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    swapchain_desc.SampleDesc.Count = 1;
    swapchain_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swapchain_desc.BufferCount = 2;
    swapchain_desc.Scaling = DXGI_SCALING_STRETCH;
    swapchain_desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    swapchain_desc.AlphaMode = DXGI_ALPHA_MODE_UNSPECIFIED;

    IDXGISwapChain1* raw_swapchain = nullptr;
    hr = factory->CreateSwapChainForHwnd(
        queue.get(), window.get(), &swapchain_desc, nullptr, nullptr, &raw_swapchain);
    comfy_dlss::com_ptr<IDXGISwapChain1> swapchain{raw_swapchain};
    if (FAILED(hr) || !swapchain) {
        result.error = "IDXGIFactory4::CreateSwapChainForHwnd failed: " + hex_code(static_cast<unsigned long>(hr));
        return 50;
    }
    result.swapchain_created = true;
    factory->MakeWindowAssociation(window.get(), DXGI_MWA_NO_ALT_ENTER);

    if (run_worker) {
        // A non-rendering frame worker does not need to fill a flip-model queue,
        // which can back-pressure indefinitely under an unattended Xwayland session.
        // Add-on presence is checked after this skipped heartbeat and still fails closed.
        result.renodx_loaded = GetModuleHandleW(L"renodx-dlss5.addon64") != nullptr;
        result.presents_requested = 0;
    }
    result.stage = "present-heartbeat";
    bool quit_requested = false;
    for (unsigned int index = 0; index < result.presents_requested && !quit_requested; ++index) {
        MSG message{};
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != FALSE) {
            if (message.message == WM_QUIT) {
                quit_requested = true;
                break;
            }
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        if (quit_requested) break;
        result.last_present_result = swapchain->Present(0, 0);
        if (FAILED(result.last_present_result)) {
            result.error = "IDXGISwapChain1::Present failed: " +
                hex_code(static_cast<unsigned long>(result.last_present_result));
            return 51;
        }
        ++result.presents_completed;
        if (GetModuleHandleW(L"renodx-dlss5.addon64") != nullptr) result.renodx_loaded = true;
        if (interval_ms > 0) {
            MsgWaitForMultipleObjectsEx(0, nullptr, interval_ms, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }
    }
    if (quit_requested) {
        result.error = "carrier window received WM_QUIT before the requested Present count completed";
        return 52;
    }

    result.reshade_log_present = file_exists(directory / L"ReShade.log");
    result.reshade_log_mentions_renodx =
        result.reshade_log_present && file_contains_case_insensitive(directory / L"ReShade.log", "renodx");
    result.wine_x11_driver_loaded = GetModuleHandleW(L"winex11.drv") != nullptr;
    result.wine_wayland_driver_loaded = GetModuleHandleW(L"winewayland.drv") != nullptr;
    if (!result.renodx_loaded) {
        result.error = "ReShade initialized, but renodx-dlss5.addon64 was not loaded";
        return 53;
    }
    if (run_ngx) {
        result.stage = "ngx-synthetic-smoke";
#if defined(COMFY_DLSS_ENABLE_NGX)
        comfy_dlss::run_ngx_smoke(
            directory.c_str(), device.get(), queue.get(), ngx_evaluations, result.ngx);
        if (!result.ngx.ok()) {
            result.error = result.ngx.error.empty() ? "NGX synthetic smoke test failed" : result.ngx.error;
            return 54;
        }
#else
        result.ngx.requested = true;
        result.ngx.evaluations_requested = ngx_evaluations;
        result.ngx.error = "this carrier was built without COMFY_DLSS_ENABLE_NGX";
        result.error = result.ngx.error;
        return 55;
#endif
    }
    if (run_worker) {
        result.frame_worker_requested = true;
        result.stage = "frame-worker";
        const bool worker_ok = worker_tcp_host != nullptr
            ? comfy_dlss::run_frame_worker_tcp(
                worker_tcp_host,
                worker_tcp_port,
                worker_tcp_token,
                device.get(),
                queue.get(),
                result.error)
            : comfy_dlss::run_frame_worker(stdin, stdout, device.get(), queue.get(), result.error);
        if (!worker_ok) {
            return 56;
        }
        result.frame_worker_completed = true;
        result.stage = "frame-worker-stopped";
        return 0;
    }
    result.stage = "bootstrap-ready";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    CarrierResult result;
    unsigned int interval_ms = 8;
    unsigned int ngx_evaluations = 30;
    bool run_ngx = false;
    bool run_worker = false;
    const char* worker_tcp_host = nullptr;
    unsigned int worker_tcp_port = 0;
    std::string worker_tcp_token;
    const char* result_path = nullptr;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument = argv[index];
        if (argument == "--result" && index + 1 < argc) {
            result_path = argv[++index];
        } else if (argument == "--presents" && index + 1 < argc) {
            if (!parse_unsigned(argv[++index], 1, 10000, result.presents_requested)) {
                result.error = "--presents must be an integer from 1 through 10000";
                break;
            }
        } else if (argument == "--interval-ms" && index + 1 < argc) {
            if (!parse_unsigned(argv[++index], 0, 1000, interval_ms)) {
                result.error = "--interval-ms must be an integer from 0 through 1000";
                break;
            }
        } else if (argument == "--ngx-smoke") {
            run_ngx = true;
        } else if (argument == "--frame-worker") {
            run_worker = true;
        } else if (argument == "--tcp" && index + 3 < argc) {
            worker_tcp_host = argv[++index];
            if (std::string_view(worker_tcp_host) != "127.0.0.1" ||
                !parse_unsigned(argv[++index], 1, 65535, worker_tcp_port)) {
                result.error = "--tcp host must be 127.0.0.1 and port must be 1..65535";
                break;
            }
            worker_tcp_token = argv[++index];
            if (worker_tcp_token.size() != 64 || !std::all_of(
                    worker_tcp_token.begin(), worker_tcp_token.end(), [](unsigned char character) {
                        return std::isxdigit(character) != 0;
                    })) {
                result.error = "--tcp token must contain exactly 64 hexadecimal characters";
                break;
            }
            run_worker = true;
        } else if (argument == "--ngx-evaluations" && index + 1 < argc) {
            if (!parse_unsigned(argv[++index], 1, 1000, ngx_evaluations)) {
                result.error = "--ngx-evaluations must be an integer from 1 through 1000";
                break;
            }
        } else {
            result.error = "usage: dlss-carrier.exe [--result PATH] [--presents 1..10000] "
                "[--interval-ms 0..1000] [--ngx-smoke] [--ngx-evaluations 1..1000] "
                "[--frame-worker] [--tcp 127.0.0.1 PORT TOKEN_HEX]";
            break;
        }
    }

    int exit_code = 2;
    if (run_worker && worker_tcp_host == nullptr) {
        if (_setmode(_fileno(stdin), _O_BINARY) == -1 ||
            _setmode(_fileno(stdout), _O_BINARY) == -1) {
            result.error = "failed to put stdin/stdout into binary mode";
        }
    }
    if (result.error.empty()) {
        exit_code = run_carrier(
            interval_ms,
            run_ngx,
            run_worker,
            worker_tcp_host,
            static_cast<std::uint16_t>(worker_tcp_port),
            worker_tcp_token,
            ngx_evaluations,
            result);
    }

    std::unique_ptr<FILE, FileCloser> owned_output;
    FILE* output = run_worker ? stderr : stdout;
    if (result_path) {
        owned_output.reset(std::fopen(result_path, "wb"));
        if (!owned_output) return 60;
        output = owned_output.get();
    }
    write_result(output, result);
    if (std::fflush(output) != 0) return 61;
    return exit_code;
}
