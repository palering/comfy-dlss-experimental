#include "probe_report.hpp"
#include "worker_connection.hpp"
#include "sr_service.hpp"
#include "sr_wire.hpp"
#ifdef COMFY_DLSS_ENABLE_SR
#include "sr_engine.hpp"
#endif
#include <array>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace comfy_dlss {
namespace {
#ifdef COMFY_DLSS_ENABLE_SR
class sr_error_reporter final {
    worker_connection& channel_;
    probe_report& report_;
    const sr_wire::header& request_;
    owned_wire::error_info info_{};
    bool emitted_ = false;
    static owned_wire::error_stage stage(std::string_view name, owned_wire::error_stage fallback) noexcept {
        using e = owned_wire::error_stage;
        if (name == "sr_device") return e::device;
        if (name == "sr_init") return e::init;
        if (name == "sr_create") return e::create;
        if (name == "sr_create_fence") return e::create_fence;
        if (name == "sr_evaluate") return e::evaluate;
        if (name == "sr_evaluate_fence") return e::evaluate_fence;
        if (name == "sr_release" || name == "sr_destroy_parameters" || name == "sr_destroy_capabilities") return e::release;
        if (name == "sr_shutdown" || name == "sr_cleanup") return e::shutdown;
        return fallback;
    }
    static void observed(void* context, const char* name, const char* state, unsigned long code) noexcept {
        auto& self = *static_cast<sr_error_reporter*>(context);
        self.info_.stage = stage(name, self.info_.stage);
        if (std::string_view(state) != "failed") return;
        const std::string_view event{name};
        const bool ngx = event == "sr_release" || event == "sr_destroy_parameters" ||
            event == "sr_destroy_capabilities" || event == "sr_shutdown";
        self.info_.code = static_cast<std::uint32_t>(code);
        self.info_.domain = ngx ? owned_wire::error_domain::ngx :
            (code != WAIT_FAILED && (code & 0x80000000UL)) ? owned_wire::error_domain::hresult : owned_wire::error_domain::wait;
        self.info_.message = ngx ? "SR NGX cleanup failed" : "SR GPU completion unverified; Worker terminated safely";
        self.send();
    }
    void send() noexcept {
        if (emitted_ || !request_.request) return;
        emitted_ = true;
        try {
            std::array<unsigned char, owned_wire::max_error_bytes> payload{};
            const auto size = owned_wire::encode_error(payload, info_);
            channel_.write(sr_wire::encode_header({sr_wire::type::error, static_cast<std::uint32_t>(size),
                request_.request, request_.session}), 250);
            channel_.write(std::span(payload).first(size), 250);
        } catch (...) {} // Never unwind through GPU owners from diagnostics.
    }
public:
    sr_error_reporter(worker_connection& channel, probe_report& report, const sr_wire::header& request)
        : channel_(channel), report_(report), request_(request) { report_.observe(&observed, this); }
    ~sr_error_reporter() { report_.observe(nullptr, nullptr); }
    sr_error_reporter(const sr_error_reporter&) = delete;
    sr_error_reporter& operator=(const sr_error_reporter&) = delete;
    void progress(owned_wire::error_stage stage_value, std::uint64_t frame, std::uint64_t evaluation) noexcept {
        info_ = {stage_value, owned_wire::error_domain::worker, 1, frame, evaluation, "SR Worker operation failed"};
    }
    void exception(const std::exception& error) noexcept {
        const std::string_view what{error.what()};
        // Only fixed operation names and parsed numeric native error codes may
        // leave this process; exception text can contain paths or user values.
        constexpr std::array<std::string_view, 9> ngx_operations{
            "SR NGX SDK initialization", "SR query capabilities", "SR capability availability",
            "SR optimal settings query", "SR allocate parameters", "SR scratch size",
            "SR create DLSS feature", "SR evaluate DLSS feature", "SR SDK operation"};
        constexpr std::array<std::string_view, 19> graphics_operations{
            "SR command list close", "SR allocator reset", "SR command list reset", "SR create texture",
            "SR create transfer buffer", "SR map upload", "SR create DXGI factory", "SR enumerate adapters",
            "SR adapter description", "SR create queue", "SR create allocator", "SR create command list",
            "SR create fence", "SR create scratch", "SR map readback", "SR create device",
            "SR signal", "SR completion event", "SR GPU operation"};
        const auto split = what.find(": 0x");
        if (split != std::string_view::npos && what.size() == split + 12) {
            const auto name = what.substr(0, split);
            bool known = false;
            for (const auto operation : ngx_operations) if (name == operation) {
                known = true; info_.domain = owned_wire::error_domain::ngx;
                info_.message = "SR NGX SDK operation failed"; break;
            }
            for (const auto operation : graphics_operations) if (name == operation) {
                known = true; info_.domain = owned_wire::error_domain::hresult;
                info_.message = "SR D3D12 operation failed"; break;
            }
            if (known) {
                std::uint32_t code = 0;
                for (const auto c : what.substr(split + 4)) {
                    unsigned int digit = 16;
                    if (c >= '0' && c <= '9') digit = static_cast<unsigned int>(c - '0');
                    else if (c >= 'a' && c <= 'f') digit = static_cast<unsigned int>(c - 'a') + 10;
                    else if (c >= 'A' && c <= 'F') digit = static_cast<unsigned int>(c - 'A') + 10;
                    if (digit > 15) { known = false; break; }
                    code = (code << 4) | digit;
                }
                if (known) info_.code = code;
            }
        } else if (what == "The NGX SDK reports that SR needs an updated NVIDIA driver")
            info_.message = "SR requires an updated NVIDIA driver";
        else if (what == "The NGX SDK reports SR unsupported on this device/runtime")
            info_.message = "SR is unavailable on this device/runtime";
        else if (what == "Explicit SR input dimensions are outside the runtime-reported quality-mode range")
            info_.message = "SR input size is outside the runtime-reported quality-mode range";
        else if (what == "SR requires an NVIDIA D3D12 hardware device") {
            info_.stage = owned_wire::error_stage::device;
            info_.message = "SR requires an NVIDIA D3D12 hardware device";
        } else if (info_.stage == owned_wire::error_stage::protocol)
            info_.message = "Worker rejected SR request state or input contract";
        else if (info_.stage == owned_wire::error_stage::transport)
            info_ = owned_wire::describe_exception(what, info_);
        send();
    }
};
#endif
}

int serve_sr(const std::filesystem::path& model_directory, const std::filesystem::path& logs,
             const std::string& project_id, unsigned short port, std::wstring_view token,
             probe_report& report, refresh_watchdog refresh, void* watchdog) {
    namespace wire = sr_wire;
    if (!refresh || !watchdog) throw std::invalid_argument("SR watchdog missing");
    worker_connection channel;
    channel.connect_local(port, token);
    auto send = [&](wire::type kind, std::uint64_t request, std::uint64_t session,
                    std::span<const unsigned char> data) {
        if (data.size() > wire::max_payload) throw std::invalid_argument("Oversized SR response");
        channel.write(wire::encode_header({kind, static_cast<std::uint32_t>(data.size()), request, session}));
        channel.write(data);
    };
#ifndef COMFY_DLSS_ENABLE_SR
    static_cast<void>(model_directory); static_cast<void>(logs); static_cast<void>(project_id);
    send(wire::type::caps, 0, 0, wire::capabilities(false));
    report.event("sr_not_compiled", "failed", 2);
    return 2;
#else
    send(wire::type::caps, 0, 0, wire::capabilities(true));
    std::unique_ptr<sr_engine> engine;
    sr::settings settings{};
    std::vector<unsigned char> payload, result;
    std::uint64_t expected_request = 1, session = 0, previous_pts = 0;
    std::uint32_t evaluations = 0, task_frames = 0;
    wire::header current{};
    sr_error_reporter diagnostics{channel, report, current};
    try {
        for (;;) {
            refresh(watchdog, 990000);
            diagnostics.progress(owned_wire::error_stage::transport, task_frames, evaluations);
            std::array<unsigned char, wire::header_bytes> header{};
            channel.read(header, 960000);
            refresh(watchdog, 120000);
            current = wire::parse_header(header);
            diagnostics.progress(owned_wire::error_stage::protocol, task_frames, evaluations);
            wire::validate_request(current, expected_request, static_cast<bool>(engine), session,
                engine ? wire::frame_bytes(settings) : 0, evaluations, task_frames);
            ++expected_request;
            payload.resize(current.bytes);
            channel.read(payload);
            if (current.kind == wire::type::create) {
                settings = wire::parse_settings(payload);
                if (!sr::valid_project_id(project_id)) throw std::invalid_argument("Invalid SR project ID");
                result.resize(8 + sr::output_bytes(settings));
                diagnostics.progress(owned_wire::error_stage::create, 0, 0);
                engine = std::make_unique<sr_engine>(settings, report);
                engine->initialize(model_directory, logs, project_id);
                session = current.session; evaluations = 0; task_frames = 0; previous_pts = 0;
            } else if (current.kind == wire::type::frame) {
                const auto metadata = wire::parse_frame(std::span(payload).first(wire::frame_header_bytes),
                    task_frames, previous_pts);
                const auto color_size = sr::input_bytes(settings, 8), motion_size = sr::input_bytes(settings, 4);
                const auto bytes = std::span<const unsigned char>(payload);
                const auto color = bytes.subspan(wire::frame_header_bytes, color_size);
                const auto motion = bytes.subspan(wire::frame_header_bytes + color_size, motion_size);
                const auto depth = bytes.subspan(wire::frame_header_bytes + color_size + motion_size);
                auto output = std::span(result).subspan(8);
                // Validate on CPU before reporting an Evaluate stage or mutating
                // resource state. The engine independently enforces the contract.
                sr::validate_planes(settings, color, motion, depth, output);
                diagnostics.progress(owned_wire::error_stage::evaluate, task_frames, evaluations);
                refresh(watchdog, 120000);
                engine->evaluate_frame(color, motion, depth, metadata.value, output);
                ++evaluations; ++task_frames; previous_pts = metadata.pts_ns;
                wire::put64(result, 0, metadata.pts_ns);
                send(wire::type::result, current.request, session, result);
                continue;
            } else if (current.kind == wire::type::end) {
                task_frames = 0; previous_pts = 0;
            } else if (current.kind == wire::type::release || current.kind == wire::type::shutdown) {
                diagnostics.progress(owned_wire::error_stage::release, task_frames, evaluations);
                if (engine) engine->close();
                engine.reset(); session = 0; payload.clear(); result.clear();
            }
            send(wire::type::ack, current.request, current.session, {});
            if (current.kind == wire::type::shutdown) return 0;
        }
    } catch (const std::exception& error) {
        diagnostics.exception(error);
        engine.reset(); // Every submitted GPU command has completed or the engine exited.
        return 1;
    }
#endif
}
}
