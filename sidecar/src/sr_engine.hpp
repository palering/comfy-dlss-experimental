#pragma once
#include "sr_input_contract.hpp"
#include <filesystem>
#include <memory>
#include <span>
#include <string>

namespace comfy_dlss {
class probe_report;

// Uses the official NGX SDK public API linked at build time. nvngx_dlss.dll is
// discovered by that SDK; neither the NR caller shim nor driver nvngx.dll is
// used as a guessed public API provider. No third-party C++ ABI crosses here.
class sr_engine final {
    class implementation;
    std::unique_ptr<implementation> implementation_;
public:
    sr_engine(const sr::settings& settings, probe_report& report);
    ~sr_engine();
    sr_engine(const sr_engine&) = delete;
    sr_engine& operator=(const sr_engine&) = delete;
    void initialize(const std::filesystem::path& model_directory,
                    const std::filesystem::path& logs, const std::string& project_id);
    void evaluate_frame(std::span<const unsigned char> color,
                        std::span<const unsigned char> motion,
                        std::span<const unsigned char> depth,
                        const sr::frame_info& frame, std::span<unsigned char> output);
    void close();
};
}
