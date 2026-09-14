#pragma once
#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>

namespace comfy_dlss {
class probe_report;
using refresh_watchdog = void (*)(void*, std::uint64_t) noexcept;

// The non-SDK build authenticates and reports compiled=false without loading any
// graphics module. The SDK-enabled service uses the same executable and watchdog.
int serve_sr(const std::filesystem::path& model_directory, const std::filesystem::path& logs,
             const std::string& project_id, unsigned short port, std::wstring_view token,
             probe_report& report, refresh_watchdog refresh, void* watchdog);
}
