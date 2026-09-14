#pragma once

#include "sl_sr_input_contract.hpp"
#include "sl_rr_input_contract.hpp"
#include <filesystem>
#include <memory>
#include <string>

namespace comfy_dlss {
class probe_report;
enum class sl_reconstruction_feature { super_resolution, ray_reconstruction };

// Isolated D3D12 backend using the published Streamline function-pointer API.
// Not substituted for owned_sr/CSR1: that older wire does not carry camera data.
// A hidden swapchain drives mandatory SL bookkeeping; output remains an owned
// offscreen texture/readback. No Comfy, decoding, model estimation or CLI here.
class sl_sr_engine final {
    class implementation;
    std::unique_ptr<implementation> implementation_;
public:
    sl_sr_engine(const sr::settings& settings, probe_report& report,
                 sl_reconstruction_feature feature = sl_reconstruction_feature::super_resolution);
    ~sl_sr_engine();
    sl_sr_engine(const sl_sr_engine&) = delete;
    sl_sr_engine& operator=(const sl_sr_engine&) = delete;
    void initialize(const std::filesystem::path& runtime, const std::filesystem::path& logs,
                    const std::string& project_id);
    void present_only(); // Bounded pre-Evaluate lifecycle diagnostic.
    void evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
                        std::span<const unsigned char> depth, const sl_sr::frame_info& frame,
                        const sl_sr::camera_frame& camera, std::span<unsigned char> output);
    void evaluate_rr_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
                           std::span<const unsigned char> depth, const sl_sr::frame_info& frame,
                           const sl_sr::camera_frame& camera, const sl_rr::guides& guides,
                           std::span<unsigned char> output);
    void close();
};
}
