#include "probe_report.hpp"
#include "worker_connection.hpp"
#include "sl_wire.hpp"
#include "sl_sr_engine.hpp"
#include "win32_raii.hpp"
#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace {
using namespace comfy_dlss;
namespace wire = sl_wire;

class watchdog final {
    unique_handle done_, thread_;
    std::atomic<ULONGLONG> deadline_{0};
    static DWORD WINAPI run(void* pointer) noexcept {
        auto& self = *static_cast<watchdog*>(pointer);
        for (;;) {
            const auto result = WaitForSingleObject(self.done_.get(), 1000);
            if (result == WAIT_OBJECT_0) return 0;
            if (result != WAIT_TIMEOUT || GetTickCount64() >= self.deadline_.load()) {
                TerminateProcess(GetCurrentProcess(), 73); std::_Exit(73);
            }
        }
    }
public:
    watchdog() {
        done_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!done_) throw std::runtime_error("Watchdog event failed");
        refresh(120000);
        thread_.reset(CreateThread(nullptr, 0, &run, this, 0, nullptr));
        if (!thread_) throw std::runtime_error("Watchdog thread failed");
    }
    ~watchdog() {
        if (!SetEvent(done_.get()) || WaitForSingleObject(thread_.get(), 5000) != WAIT_OBJECT_0) std::_Exit(73);
    }
    void refresh(ULONGLONG milliseconds) noexcept { deadline_.store(GetTickCount64() + milliseconds); }
};

class diagnostics final {
    worker_connection& channel_;
    probe_report& report_;
    const wire::header& request_;
    owned_wire::error_info info_{};
    bool emitted_ = false;
    static void observed(void* context, const char* name, const char* state, unsigned long code) noexcept {
        auto& self = *static_cast<diagnostics*>(context);
        if (std::string_view(state) != "failed") return;
        self.info_.code = static_cast<std::uint32_t>(code);
        // CER1 has no Streamline domain. Keep this a Worker diagnostic, not an
        // incorrectly labelled NGX result. Fixed ASCII operation names only.
        const std::string_view operation{name};
        if (operation == "sl_sr_free" || operation == "sl_rr_hard_reset") self.info_.stage = owned_wire::error_stage::release;
        if (operation == "sl_sr_shutdown" || operation == "sl_sr_cleanup") self.info_.stage = owned_wire::error_stage::shutdown;
        self.info_.message = "Streamline operation or GPU completion failed; Worker stopped";
        self.send();
    }
public:
    diagnostics(worker_connection& channel, probe_report& report, const wire::header& request)
        : channel_(channel), report_(report), request_(request) { report_.observe(&observed, this); }
    ~diagnostics() { report_.observe(nullptr, nullptr); }
    void progress(owned_wire::error_stage stage, std::uint64_t frames, std::uint64_t evaluations) noexcept {
        info_ = {stage, owned_wire::error_domain::worker, 1, frames, evaluations,
                 stage == owned_wire::error_stage::protocol ? "Worker rejected reconstruction state or input contract" :
                 stage == owned_wire::error_stage::create ? "Streamline reconstruction initialization failed; inspect local Worker log" :
                 "Reconstruction Worker operation failed; inspect local Worker log"};
    }
    void send() noexcept {
        if (emitted_ || !request_.request) return;
        emitted_ = true;
        try {
            std::array<unsigned char, owned_wire::max_error_bytes> bytes{};
            const auto count = owned_wire::encode_error(bytes, info_);
            channel_.write(wire::encode_header({wire::type::error, static_cast<std::uint32_t>(count),
                request_.request, request_.session}), 250);
            channel_.write(std::span(bytes).first(count), 250);
        } catch (...) {}
    }
};

int serve(const std::filesystem::path& runtime, const std::filesystem::path& logs,
          const std::string& project, unsigned short port, std::wstring_view token,
          probe_report& report, watchdog& timer) {
    worker_connection channel;
    channel.connect_local(port, token);
    auto send = [&](wire::type type, std::uint64_t request, std::uint64_t session,
                    std::span<const unsigned char> data) {
        if (data.size() > wire::max_payload) throw std::invalid_argument("Oversized reconstruction response");
        channel.write(wire::encode_header({type, static_cast<std::uint32_t>(data.size()), request, session}));
        channel.write(data);
    };
    send(wire::type::caps, 0, 0, wire::capabilities());
    wire::header current{};
    diagnostics errors{channel, report, current};
    std::unique_ptr<sl_sr_engine> engine;
    wire::settings settings{};
    std::vector<unsigned char> payload, result;
    std::uint64_t expected_request = 1, session = 0, previous_pts = 0;
    std::uint32_t evaluations = 0, frames = 0;
    try {
        for (;;) {
            timer.refresh(990000);
            errors.progress(owned_wire::error_stage::transport, frames, evaluations);
            std::array<unsigned char, wire::header_bytes> bytes{};
            channel.read(bytes, 960000);
            timer.refresh(120000);
            current = wire::parse_header(bytes);
            errors.progress(owned_wire::error_stage::protocol, frames, evaluations);
            wire::validate_request(current, expected_request, static_cast<bool>(engine), session,
                engine ? wire::frame_bytes(settings) : 0, evaluations, frames);
            ++expected_request;
            payload.resize(current.bytes); channel.read(payload);
            if (current.kind == wire::type::create) {
                settings = wire::parse_settings(payload);
                result.resize(8 + sr::output_bytes(settings.common));
                errors.progress(owned_wire::error_stage::create, 0, 0);
                engine = std::make_unique<sl_sr_engine>(settings.common, report, settings.rr ?
                    sl_reconstruction_feature::ray_reconstruction : sl_reconstruction_feature::super_resolution);
                engine->initialize(runtime, logs, project);
                session = current.session; evaluations = frames = 0; previous_pts = 0;
            } else if (current.kind == wire::type::frame) {
                const auto data = std::span<const unsigned char>(payload);
                const auto frame = wire::parse_frame(data.first(wire::frame_header_bytes), settings, frames, previous_pts);
                const auto camera = wire::parse_camera(data.subspan(wire::frame_header_bytes, wire::camera_bytes), settings);
                constexpr auto offset = wire::frame_header_bytes + wire::camera_bytes;
                const auto color_bytes = sr::input_bytes(settings.common, 8), other_bytes = sr::input_bytes(settings.common, 4);
                const auto color = data.subspan(offset, color_bytes), motion = data.subspan(offset + color_bytes, other_bytes);
                const auto depth = data.subspan(offset + color_bytes + other_bytes, other_bytes);
                auto output = std::span(result).subspan(8);
                sr::validate_planes(settings.common, color, motion, depth, output);
                auto rr = settings.rr ? wire::parse_rr(data.subspan(offset + color_bytes + 2 * other_bytes), settings, frame.exposure) : sl_rr::guides{};
                errors.progress(owned_wire::error_stage::evaluate, frames, evaluations);
                if (settings.rr) engine->evaluate_rr_frame(color, motion, depth, frame.info, camera, rr, output);
                else engine->evaluate_frame(color, motion, depth, frame.info, camera, output);
                ++evaluations; ++frames; previous_pts = frame.pts_ns;
                wire::put64(result, 0, frame.pts_ns);
                send(wire::type::result, current.request, session, result);
                continue;
            } else if (current.kind == wire::type::end) {
                frames = 0; previous_pts = 0;
            } else if (current.kind == wire::type::release || current.kind == wire::type::shutdown) {
                errors.progress(owned_wire::error_stage::release, frames, evaluations);
                if (engine) engine->close();
                engine.reset(); session = 0; result.clear(); payload.clear();
            }
            send(wire::type::ack, current.request, current.session, {});
            if (current.kind == wire::type::shutdown) return 0;
        }
    } catch (const std::exception&) {
        errors.send();
        engine.reset(); // Graphics engine independently fences or terminates on uncertainty.
        report.event("sl_worker", "failed", 1);
        return 1;
    }
}
}

int wmain(int argc, wchar_t** argv) {
    try {
        watchdog timer;
        probe_report report;
        if (argc != 7 || std::wstring_view(argv[1]) != L"--serve-sl")
            throw std::invalid_argument("Expected explicit reconstruction service arguments");
        const std::wstring raw_project{argv[4]};
        if (raw_project.size() != 36) throw std::invalid_argument("Invalid project UUID");
        std::string project;
        for (auto c : raw_project) {
            if (c > 127) throw std::invalid_argument("Invalid project UUID");
            project.push_back(static_cast<char>(c));
        }
        if (!sr::valid_project_id(project)) throw std::invalid_argument("Invalid project UUID");
        const std::wstring_view raw_port{argv[5]};
        if (raw_port.empty() || raw_port.size() > 5) throw std::invalid_argument("Invalid port");
        unsigned int port = 0;
        for (auto c : raw_port) {
            if (c < L'0' || c > L'9') throw std::invalid_argument("Invalid port");
            port = port * 10 + static_cast<unsigned int>(c - L'0');
        }
        if (!port || port > 65535) throw std::invalid_argument("Invalid port");
        const auto code = serve(argv[2], argv[3], project, static_cast<unsigned short>(port), argv[6], report, timer);
        report.complete(static_cast<unsigned long>(code));
        return code;
    } catch (...) {
        std::fputs("Reconstruction Worker startup failed\n", stderr);
        return 1;
    }
}
