// Owned D3D12/NGX acceptance host. This mode deliberately does not claim a video
// protocol: first prove the exact model/caller/Parameter ABI on the target GPU.
#include "probe_report.hpp"
#include "worker_connection.hpp"
#include "owned_wire.hpp"
#include "nr_engine.hpp"
#include "nr_sequence_io.hpp"
#include "sr_service.hpp"
#include "win32_raii.hpp"
#include <array>
#include <atomic>
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

extern "C" uint32_t ComfyNR_ParameterAbiCheck();

namespace {
using namespace comfy_dlss;
using nr_probe = comfy_dlss::nr_engine;

class probe_watchdog final {
    unique_handle done_, thread_;
    std::atomic<ULONGLONG> deadline_{0};
    static DWORD WINAPI watch(void* context) noexcept {
        auto& self = *static_cast<probe_watchdog*>(context);
        for (;;) {
            const auto result = WaitForSingleObject(self.done_.get(), 1000);
            if (result == WAIT_OBJECT_0) return 0;
            if (result != WAIT_TIMEOUT || GetTickCount64() >= self.deadline_.load()) {
                // Never let console I/O defeat the watchdog.
                TerminateProcess(GetCurrentProcess(), 73);
                std::_Exit(73);
            }
        }
    }
public:
    probe_watchdog() {
        done_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!done_) throw std::runtime_error("Create watchdog event failed");
        refresh();
        thread_.reset(CreateThread(nullptr, 0, &watch, this, 0, nullptr));
        if (!thread_) throw std::runtime_error("Create watchdog thread failed");
    }
    ~probe_watchdog() {
        if (!SetEvent(done_.get()) || WaitForSingleObject(thread_.get(), 5000) != WAIT_OBJECT_0)
            std::_Exit(73);
    }
    probe_watchdog(const probe_watchdog&) = delete;
    probe_watchdog& operator=(const probe_watchdog&) = delete;
    void refresh(ULONGLONG milliseconds = 120000) noexcept { deadline_.store(GetTickCount64() + milliseconds); }
};


unsigned int positive(const wchar_t* text, unsigned int minimum, unsigned int maximum) {
    unsigned int value = 0;
    if (!text || !*text) throw std::runtime_error("Empty integer argument");
    for (const wchar_t* p = text; *p; ++p) {
        if (*p < L'0' || *p > L'9' || value > maximum / 10) throw std::runtime_error("Invalid integer argument");
        value = value * 10 + static_cast<unsigned int>(*p - L'0');
        if (value > maximum) throw std::runtime_error("Integer exceeds probe limit");
    }
    if (value < minimum) throw std::runtime_error("Integer below probe limit");
    return value;
}

class owned_error_reporter final {
    worker_connection& channel_;
    probe_report& report_;
    const owned_wire::header& request_;
    owned_wire::error_info info_{};
    bool emitted_=false;
    static void observed(void* context,const char* stage,const char* state,unsigned long code) noexcept {
        auto& self=*static_cast<owned_error_reporter*>(context);
        using stage_type=owned_wire::error_stage;
        const std::string_view name{stage}, status{state};
        self.info_.stage=name=="model_load" ? stage_type::load : owned_wire::stage_from_event(name,self.info_.stage);
        if(name=="evaluate" && status=="begin") self.info_.evaluation=code;
        const bool ngx=name=="init" || name=="create" || name=="evaluate" || name=="release" || name=="shutdown";
        if(status=="failed" || (ngx && status=="returned" && code!=1)) {
            self.info_.code=static_cast<std::uint32_t>(code);
            self.info_.domain=ngx ? owned_wire::error_domain::ngx :
                (code!=WAIT_FAILED && (code&0x80000000UL)) ? owned_wire::error_domain::hresult : owned_wire::error_domain::wait;
            self.info_.message=ngx ? "NGX operation failed" : "GPU completion unverified; Worker terminated safely";
            self.send(); // Includes fatal fence/release/shutdown paths before _Exit.
        }
    }
    void send() noexcept {
        if(emitted_ || !request_.request) return;
        // At most one diagnostic attempt; do not append another packet after a
        // partially written error. Allocation-free and bounded even on failure.
        emitted_=true;
        try {
            std::array<unsigned char,owned_wire::max_error_bytes> payload{};
            const auto size=owned_wire::encode_error(payload,info_);
            channel_.write(owned_wire::encode_header({owned_wire::type::error,static_cast<std::uint32_t>(size),
                request_.request,request_.session}),250);
            channel_.write(std::span(payload).first(size),250);
        } catch(...) {} // Diagnostics may never unwind through in-flight GPU owners.
    }
public:
    owned_error_reporter(worker_connection& channel,probe_report& report,const owned_wire::header& request)
        : channel_(channel),report_(report),request_(request) { report_.observe(&observed,this); }
    ~owned_error_reporter() { report_.observe(nullptr,nullptr); }
    owned_error_reporter(const owned_error_reporter&)=delete;
    owned_error_reporter& operator=(const owned_error_reporter&)=delete;
    void progress(owned_wire::error_stage stage,std::uint64_t frame,std::uint64_t evaluation) noexcept {
        info_={stage,owned_wire::error_domain::worker,1,frame,evaluation,"Worker operation failed"};
    }
    void exception(const std::exception& error) noexcept {
        info_=owned_wire::describe_exception(error.what(),info_); send();
    }
};
}

int execute_probe(int argc, wchar_t** argv, comfy_dlss::probe_report& report, probe_watchdog& watchdog) {
    if (argc == 2 && std::wstring_view(argv[1]) == L"--self-test-parameter-abi") {
        report.event("parameter_abi", "begin");
        const auto code = ComfyNR_ParameterAbiCheck();
        report.event("parameter_abi", "returned", code);
        if (!report.connected()) std::printf("{\"mode\":\"parameter_abi_check\",\"error_bits\":%u,\"gpu_started\":false}\n", code);
        return code ? 1 : 0;
    }
    if (argc == 2 && std::wstring_view(argv[1]) == L"--self-test-report-failure")
        throw std::runtime_error("Intentional GPU-free report failure");
    if (argc == 2 && std::wstring_view(argv[1]) == L"--probe-device") {
        nr_probe probe{64, 64, report};
        probe.initialize_device();
        return 0;
    }
    const auto mode = argc > 1 ? std::wstring_view(argv[1]) : std::wstring_view{};
    if (mode == L"--serve-sr" && argc == 7) {
        std::string project_id;
        for (const wchar_t c : std::wstring_view(argv[4])) {
            if (c > 127 || project_id.size() >= 36)
                throw std::runtime_error("Invalid SR project ID argument");
            project_id.push_back(static_cast<char>(c));
        }
        return comfy_dlss::serve_sr(argv[2], argv[3], project_id,
            static_cast<unsigned short>(positive(argv[5], 1, 65535)), argv[6], report,
            [](void* context, std::uint64_t milliseconds) noexcept {
                static_cast<probe_watchdog*>(context)->refresh(milliseconds);
            }, &watchdog);
    }
    if (mode == L"--serve-nr" && argc == 7) {
        namespace wire = comfy_dlss::owned_wire;
        const std::filesystem::path logs{argv[4]};
        if (!logs.is_absolute() || !std::filesystem::is_directory(logs)) throw std::runtime_error("Invalid Worker logs directory");
        if (ComfyNR_ParameterAbiCheck()) throw std::runtime_error("Worker ABI check failed");
        comfy_dlss::worker_connection channel;
        channel.connect_local(static_cast<unsigned short>(positive(argv[5],1,65535)),argv[6]);
        auto send = [&](wire::type kind, std::uint64_t request, std::uint64_t session, std::span<const unsigned char> payload) {
            if (payload.size() > wire::max_payload) throw std::runtime_error("Oversized Worker response");
            channel.write(wire::encode_header({kind,static_cast<std::uint32_t>(payload.size()),request,session})); channel.write(payload);
        };
        std::array<unsigned char,32> caps{};
        // Bounds/format/features/session limits are protocol capabilities, not hardware quality claims.
        for (const auto [offset,value] : std::array<std::pair<std::size_t,std::uint32_t>,8>{{{0,64},{4,64},{8,1920},{12,1080},{16,1},{20,1},{24,wire::max_payload},{28,1}}})
            wire::put32(caps,offset,value);
        send(wire::type::caps,0,0,caps);
        std::unique_ptr<nr_probe> engine;
        std::vector<unsigned char> result, payload;
        wire::settings settings{};
        std::uint64_t session=0, previous_pts=0, expected_request=1;
        unsigned int evaluations=0, task_frames=0;
        bool first=true;
        wire::header current{};
        owned_error_reporter diagnostics{channel,report,current};
        try {
            for (;;) {
                // A resident wait is bounded independently from active GPU work.
                // Host idle policy is at most 900s. Keep a bounded grace period
                // so the host owns normal expiry rather than racing this read.
                watchdog.refresh(990000);
                diagnostics.progress(wire::error_stage::transport,task_frames,evaluations);
                std::array<unsigned char,wire::header_bytes> header{}; channel.read(header,960000);
                watchdog.refresh(); current=wire::parse_header(header);
                diagnostics.progress(wire::error_stage::protocol,task_frames,evaluations);
                if (current.request != expected_request || expected_request == UINT64_MAX)
                    throw std::runtime_error("Worker request order mismatch");
                ++expected_request;
                const auto frame_bytes = engine ? 16ULL+static_cast<std::uint64_t>(settings.width)*settings.height*12 : 0;
                if (current.kind == wire::type::create) {
                    if(engine || !current.session || current.bytes!=48) throw std::runtime_error("Invalid create state");
                } else if(current.kind == wire::type::frame) {
                    if(!engine || current.session!=session || current.bytes!=frame_bytes || evaluations>=1000000)
                        throw std::runtime_error("Invalid frame state/size");
                } else if(current.kind == wire::type::end || current.kind == wire::type::release) {
                    if(!engine || current.session!=session || current.bytes || (current.kind==wire::type::end && !task_frames))
                        throw std::runtime_error("Invalid session control");
                } else if(current.kind == wire::type::ping || current.kind == wire::type::shutdown) {
                    if(current.session || current.bytes) throw std::runtime_error("Invalid process control");
                } else throw std::runtime_error("Unexpected Worker message direction");
                payload.resize(current.bytes); channel.read(payload);
                if(current.kind == wire::type::create) {
                    settings=wire::parse_settings(payload);
                    result.resize(8ULL+static_cast<std::size_t>(settings.width)*settings.height*8);
                    diagnostics.progress(wire::error_stage::create,0,0);
                    engine=std::make_unique<nr_probe>(settings.width,settings.height,report);
                    engine->initialize(argv[2],argv[3],logs,false,&settings);
                    session=current.session; evaluations=0; task_frames=0; first=true;
                } else if(current.kind == wire::type::frame) {
                    const auto meta=sequence_probe::parse_frame(std::span(payload).first(16),task_frames,previous_pts);
                    const auto color_bytes=static_cast<std::size_t>(settings.width)*settings.height*8;
                    const auto color=std::span<const unsigned char>(payload).subspan(16,color_bytes);
                    const auto motion=std::span<const unsigned char>(payload).subspan(16+color_bytes);
                    auto image=std::span(result).subspan(8);
                    if(first) for(unsigned int n=0;n<settings.warmup;++n) {
                        diagnostics.progress(wire::error_stage::evaluate,task_frames,evaluations);
                        watchdog.refresh(); engine->evaluate_frame(color,motion,evaluations++,n==0,image);
                    }
                    diagnostics.progress(wire::error_stage::evaluate,task_frames,evaluations);
                    watchdog.refresh(); engine->evaluate_frame(color,motion,evaluations++,meta.reset,image);
                    wire::put64(result,0,meta.pts); first=false; previous_pts=meta.pts; ++task_frames;
                    send(wire::type::result,current.request,session,result); continue;
                } else if(current.kind == wire::type::end) {
                    // Keep allocations/model, but require reset and a new PTS epoch on the next task.
                    task_frames=0; previous_pts=0;
                } else if(current.kind == wire::type::release || current.kind == wire::type::shutdown) {
                    engine.reset(); session=0; payload.clear(); result.clear();
                }
                send(wire::type::ack,current.request,current.session,{});
                if(current.kind == wire::type::shutdown) return 0;
            }
        } catch(const std::exception& error) {
            diagnostics.exception(error);
            // Never continue an uncertain session. Release only after fenced work.
            engine.reset();
            return 1;
        }
    }
    if (mode == L"--probe-sequence" && argc == 7) {
        report.event("parameter_abi", "begin");
        const auto abi = ComfyNR_ParameterAbiCheck();
        report.event("parameter_abi", "returned", abi);
        if (abi) throw std::runtime_error("Microsoft parameter ABI self-check failed");
        const std::filesystem::path logs{argv[4]};
        if (!logs.is_absolute() || !std::filesystem::is_directory(logs)) throw std::runtime_error("Invalid sequence logs directory");
        sequence_probe::input input{argv[5]}; // Includes CPU-only preflight.
        sequence_probe::output output{argv[6]}; // Never overwrites an existing result.
        std::vector<unsigned char> result(input.shape.color_bytes);
        {
            nr_probe probe{input.shape.width, input.shape.height, report};
            probe.initialize(argv[2], argv[3], logs);
            unsigned int evaluation = 0;
            for (unsigned int index = 0; index < input.shape.count; ++index) {
                const auto frame = input.next();
                // Exactly one discarded first-frame warmup for this diagnostic.
                if (!index) probe.evaluate_frame(input.color, input.motion, evaluation++, true, result);
                // Warmup is an internal evaluation, not permission to discard
                // the caller's first-frame/reset contract on the delivered frame.
                probe.evaluate_frame(input.color, input.motion, evaluation++, frame.reset, result);
                output.append(result);
            }
        }
        output.finish();
        return 0;
    }
    const bool init_only = mode == L"--probe-init-parameters" && argc == 5;
    const bool create_only = mode == L"--probe-create-parameters" && argc == 7;
    const bool evaluate = mode == L"--probe-nr-init-parameters" && argc == 8;
    if (!init_only && !create_only && !evaluate) throw std::runtime_error("Invalid probe mode/arguments");
    report.event("parameter_abi", "begin");
    const auto abi_result = ComfyNR_ParameterAbiCheck();
    report.event("parameter_abi", "returned", abi_result);
    if (abi_result) throw std::runtime_error("Microsoft parameter ABI self-check failed");
    const unsigned int width = init_only ? 64 : positive(argv[5], 64, 1920);
    const unsigned int height = init_only ? 64 : positive(argv[6], 64, 1080);
    const unsigned int count = evaluate ? positive(argv[7], 1, 240) : 0;
    const std::filesystem::path logs{argv[4]};
    if (!logs.is_absolute() || !std::filesystem::is_directory(logs))
        throw std::runtime_error("Log directory must already exist and be absolute");
    unsigned long long checksum = 0;
    { // Destructor must release/shutdown successfully before reporting success.
        nr_probe probe{width, height, report};
        probe.initialize(argv[2], argv[3], logs, init_only);
        if (evaluate) checksum = probe.evaluate(count);
    }
    if (evaluate && !report.connected()) std::printf("{\"mode\":\"nr_probe\",\"evaluate_succeeded\":true,\"release_succeeded\":true,"
                "\"width\":%u,\"height\":%u,\"evaluations\":%u,\"checksum_fnv1a\":\"%016llx\","
                "\"video_protocol\":false,\"visual_acceptance\":false}\n", width, height, count, checksum);
    return 0;
}

int wmain(int argc, wchar_t** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    const bool reporting = argc >= 4 && std::wstring_view(argv[argc-3]) == L"--report";
    try {
        probe_watchdog watchdog;
        comfy_dlss::probe_report report;
        if (argc > 1 && (std::wstring_view(argv[1]) == L"--serve-nr" ||
            std::wstring_view(argv[1]) == L"--serve-sr")) report.silence_console();
        if (reporting) {
            const auto port = positive(argv[argc-2], 1, 65535);
            report.connect_local(static_cast<unsigned short>(port), argv[argc-1]);
            argc -= 3;
        }
        report.event("process", "ready");
        int code = 0;
        try { code = execute_probe(argc, argv, report, watchdog); }
        catch (const std::exception& error) {
            if (report.console_enabled()) std::fprintf(stderr, "Owned NR probe failed: %s\n", error.what());
            report.event("process", "failed", 1); code = 1;
        }
        report.complete(static_cast<unsigned long>(code));
        return code;
    } catch (const std::exception& error) {
        if (!reporting && !(argc > 1 && (std::wstring_view(argv[1]) == L"--serve-nr" ||
            std::wstring_view(argv[1]) == L"--serve-sr")))
            std::fprintf(stderr, "Owned probe startup failed: %s\n", error.what());
        return 1;
    }
}
