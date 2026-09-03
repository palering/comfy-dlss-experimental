#include <d3d12.h>

#include "win32_raii.hpp"

#include <array>
#include <cstdio>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::array<std::string_view, 10> kRequiredExports = {
    "NVSDK_NGX_D3D12_Init",
    "NVSDK_NGX_D3D12_Init_Ext",
    "NVSDK_NGX_D3D12_CreateFeature",
    "NVSDK_NGX_D3D12_EvaluateFeature",
    "NVSDK_NGX_D3D12_ReleaseFeature",
    "NVSDK_NGX_D3D12_Shutdown",
    "NVSDK_NGX_D3D12_Shutdown1",
    "NVSDK_NGX_D3D12_GetFeatureRequirements",
    "NVSDK_NGX_D3D12_GetScratchBufferSize",
    "NVSDK_NGX_D3D12_PopulateParameters_Impl",
};

std::string json_escape(std::string_view value) {
    std::string escaped;
    escaped.reserve(value.size() + 8);
    for (const unsigned char character : value) {
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
    while (!message.empty() && (message.back() == '\r' || message.back() == '\n')) message.pop_back();
    return message;
}

std::string hex_code(unsigned long code) {
    char value[16]{};
    std::snprintf(value, sizeof(value), "0x%08lx", code);
    return value;
}

void print_string_array(FILE* output, const std::vector<std::string>& values) {
    std::fprintf(output, "[");
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index) std::fprintf(output, ",");
        std::fprintf(output, "\"%s\"", json_escape(values[index]).c_str());
    }
    std::fprintf(output, "]");
}

}  // namespace

int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    const char* dll_path = argc > 1 ? argv[1] : "nvngx_dlssnr.dll";
    struct FileCloser final {
        void operator()(FILE* file) const noexcept {
            if (file) std::fclose(file);
        }
    };
    std::unique_ptr<FILE, FileCloser> owned_output;
    FILE* output = stdout;
    if (argc > 2) {
        owned_output.reset(std::fopen(argv[2], "wb"));
        if (!owned_output) return 30;
        output = owned_output.get();
    }

    bool d3d12_created = false;
    unsigned int node_count = 0;
    std::string d3d12_error;
    comfy_dlss::com_ptr<ID3D12Device> device;

    comfy_dlss::unique_module d3d12_module{LoadLibraryW(L"d3d12.dll")};
    if (!d3d12_module) {
        const DWORD code = GetLastError();
        d3d12_error = hex_code(code) + " " + win32_message(code);
    } else {
        const auto create_device = reinterpret_cast<PFN_D3D12_CREATE_DEVICE>(d3d12_module.symbol("D3D12CreateDevice"));
        if (!create_device) {
            const DWORD code = GetLastError();
            d3d12_error = "D3D12CreateDevice export unavailable: " + hex_code(code) + " " + win32_message(code);
        } else {
            void* raw_device = nullptr;
            const HRESULT result = create_device(
                nullptr,
                D3D_FEATURE_LEVEL_12_0,
                IID_ID3D12Device,
                &raw_device);
            device.reset(static_cast<ID3D12Device*>(raw_device));
            if (SUCCEEDED(result) && device) {
                d3d12_created = true;
                node_count = device->GetNodeCount();
            } else {
                d3d12_error = "D3D12CreateDevice failed: " + hex_code(static_cast<unsigned long>(result));
            }
        }
    }

    comfy_dlss::unique_module dlssnr_module{LoadLibraryA(dll_path)};
    const DWORD dll_error_code = dlssnr_module ? ERROR_SUCCESS : GetLastError();
    std::vector<std::string> present_exports;
    std::vector<std::string> missing_exports;
    if (dlssnr_module) {
        for (const std::string_view name : kRequiredExports) {
            if (dlssnr_module.symbol(std::string(name).c_str())) {
                present_exports.emplace_back(name);
            } else {
                missing_exports.emplace_back(name);
            }
        }
    }

    const bool ok = d3d12_created && dlssnr_module && missing_exports.empty();
    const std::string d3d12_error_json = d3d12_error.empty() ? "null" : "\"" + json_escape(d3d12_error) + "\"";
    const std::string dll_error_json = dlssnr_module
        ? "null"
        : "\"" + json_escape(hex_code(dll_error_code) + " " + win32_message(dll_error_code)) + "\"";

    std::fprintf(output, "{");
    std::fprintf(output, "\"schema_version\":1,");
    std::fprintf(output, "\"probe\":\"dlssnr-loader\",");
    std::fprintf(output, "\"ok\":%s,", ok ? "true" : "false");
    std::fprintf(output, "\"d3d12\":{");
    std::fprintf(output, "\"created\":%s,", d3d12_created ? "true" : "false");
    std::fprintf(output, "\"requested_feature_level\":\"12_0\",");
    std::fprintf(output, "\"node_count\":%u,", node_count);
    std::fprintf(output, "\"error\":%s", d3d12_error_json.c_str());
    std::fprintf(output, "},");
    std::fprintf(output, "\"dlssnr\":{");
    std::fprintf(output, "\"path\":\"%s\",", json_escape(dll_path).c_str());
    std::fprintf(output, "\"loaded\":%s,", dlssnr_module ? "true" : "false");
    std::fprintf(output, "\"load_error\":%s,", dll_error_json.c_str());
    std::fprintf(output, "\"present_exports\":");
    print_string_array(output, present_exports);
    std::fprintf(output, ",\"missing_exports\":");
    print_string_array(output, missing_exports);
    std::fprintf(output, "}}\n");
    if (std::fflush(output) != 0) return 31;
    if (ok) return 0;
    if (!d3d12_created) return 10;
    if (!dlssnr_module) return 20;
    return 21;
}
