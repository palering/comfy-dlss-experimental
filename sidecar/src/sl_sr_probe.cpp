#include "probe_report.hpp"
#include "sl_sr_engine.hpp"
#include "win32_raii.hpp"
#include <bit>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <filesystem>
#include <mutex>
#include <span>
#include <string>
#include <thread>
#include <vector>

namespace {
using namespace comfy_dlss;

class report_file final {
    unique_handle file_;
    std::mutex mutex_;
public:
    explicit report_file(const std::filesystem::path& path)
        : file_(CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr)) {
        if (!file_) throw std::runtime_error("Cannot create new probe report");
    }
    void write(const char* text) noexcept {
        std::lock_guard lock(mutex_);
        auto length = std::strlen(text);
        while (length) {
            DWORD done = 0;
            if (!WriteFile(file_.get(), text, static_cast<DWORD>(length), &done, nullptr) || !done) std::_Exit(73);
            text += done; length -= done;
        }
        if (!FlushFileBuffers(file_.get())) std::_Exit(73);
    }
    static void event(void* context, const char* name, const char* state, unsigned long code) noexcept {
        char line[512]{};
        std::snprintf(line, sizeof(line), "{\"event\":\"%s\",\"state\":\"%s\",\"code\":%lu}\n", name, state, code);
        static_cast<report_file*>(context)->write(line);
    }
    void error(const char* message) noexcept {
        // Error messages may contain a path. Escape control characters/quotes;
        // diagnostics remain local and are never used as shell arguments.
        char line[2048]{}; std::size_t at = 0;
        const char prefix[] = "{\"error\":\"";
        for (const auto c : std::string_view(prefix, sizeof(prefix) - 1)) line[at++] = c;
        for (const char character : std::string_view(message)) {
            const auto c = static_cast<unsigned char>(character);
            if (at + 8 >= sizeof(line)) break;
            if (c == '"' || c == '\\') { line[at++] = '\\'; line[at++] = static_cast<char>(c); }
            else if (c < 32) line[at++] = ' ';
            else line[at++] = static_cast<char>(c);
        }
        line[at++] = '"'; line[at++] = '}'; line[at++] = '\n'; line[at] = 0;
        write(line);
    }
};

class deadline final {
    std::mutex mutex_;
    std::condition_variable wake_;
    bool stopped_ = false;
    std::thread thread_;
public:
    explicit deadline(report_file& output) : thread_([this, &output] {
        std::unique_lock lock(mutex_);
        if (!wake_.wait_for(lock, std::chrono::seconds(45), [this] { return stopped_; })) {
            output.write("{\"event\":\"probe_deadline\",\"state\":\"failed\",\"code\":124}\n");
            std::_Exit(124);
        }
    }) {}
    ~deadline() {
        { std::lock_guard lock(mutex_); stopped_ = true; }
        wake_.notify_all(); thread_.join();
    }
};

void save(const std::filesystem::path& path, std::span<const unsigned char> bytes) {
    unique_handle file{CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr)};
    if (!file) throw std::runtime_error("Cannot create new probe frame output");
    while (!bytes.empty()) {
        DWORD done = 0;
        if (!WriteFile(file.get(), bytes.data(), static_cast<DWORD>(bytes.size()), &done, nullptr) || !done)
            throw std::runtime_error("Probe frame write failed");
        bytes = bytes.subspan(done);
    }
    if (!FlushFileBuffers(file.get())) throw std::runtime_error("Probe frame flush failed");
}

sl_sr::camera_frame static_camera(const sr::settings& settings) {
    // Deliberate synthetic scene: stationary, left-handed perspective camera,
    // a flat device-depth plane and a static checker texture. Not video metadata.
    sl_sr::camera_frame camera{};
    camera.near_plane = .1F; camera.far_plane = 100; camera.vertical_fov = 1.0471975512F;
    camera.aspect = static_cast<float>(settings.input_width) / static_cast<float>(settings.input_height);
    camera.up = {0, 1, 0}; camera.right = {1, 0, 0}; camera.forward = {0, 0, 1};
    const float y = 1 / std::tan(camera.vertical_fov / 2), x = y / camera.aspect;
    const float z = camera.far_plane / (camera.far_plane - camera.near_plane);
    const float w = -camera.near_plane * z;
    camera.view_to_clip = {x,0,0,0, 0,y,0,0, 0,0,z,1, 0,0,w,0};
    camera.clip_to_view = {1/x,0,0,0, 0,1/y,0,0, 0,0,0,1/w, 0,0,1,-z/w};
    for (const auto index : {0U, 5U, 10U, 15U}) camera.clip_to_previous[index] = camera.previous_to_clip[index] = 1;
    sl_sr::validate(camera, settings);
    return camera;
}

float half_float(std::uint16_t bits) {
    const int exponent = (bits >> 10) & 31;
    const auto mantissa = static_cast<float>(bits & 1023) / 1024;
    const float value = exponent ? std::ldexp(1 + mantissa, exponent - 15) : std::ldexp(mantissa, -14);
    return (bits & 0x8000U) ? -value : value;
}

void check_output(std::span<const unsigned char> output, unsigned int index, report_file& report) {
    double minimum = std::numeric_limits<double>::infinity(), maximum = -minimum;
    std::uint64_t hash = 14695981039346656037ULL;
    for (const auto value : output) { hash ^= value; hash *= 1099511628211ULL; }
    for (std::size_t at = 0; at < output.size(); at += 8) for (std::size_t channel = 0; channel < 3; ++channel) {
        const auto offset = at + channel * 2;
        const auto bits = static_cast<std::uint16_t>(output[offset] | static_cast<unsigned int>(output[offset + 1]) << 8);
        const auto value = static_cast<double>(half_float(bits));
        minimum = std::min(minimum, value); maximum = std::max(maximum, value);
    }
    if (!std::isfinite(minimum) || !std::isfinite(maximum) || maximum - minimum < .05)
        throw std::runtime_error("SR result is non-finite or effectively blank");
    char line[512]{};
    std::snprintf(line, sizeof(line), "{\"event\":\"frame_output\",\"frame\":%u,\"bytes\":%zu,\"rgb_min\":%.9g,\"rgb_max\":%.9g,\"fnv1a64\":\"%016llx\"}\n",
        index, output.size(), minimum, maximum, static_cast<unsigned long long>(hash));
    report.write(line);
}

int run(int argc, wchar_t** argv) {
    static_assert(std::endian::native == std::endian::little);
    if (argc != 5 && argc != 6) return 2; // runtime, new job dir, device|frame|sequence, UUID, optional sr|dlaa|rr|rr-dlaa
    const std::filesystem::path runtime{argv[1]}, job{argv[2]};
    const std::wstring mode{argv[3]};
    const std::wstring feature = argc == 6 ? argv[5] : L"sr";
    if (feature != L"sr" && feature != L"dlaa" && feature != L"rr" && feature != L"rr-dlaa") return 2;
    const bool rr = feature == L"rr" || feature == L"rr-dlaa";
    const bool dlaa = feature == L"dlaa" || feature == L"rr-dlaa";
    if (!runtime.is_absolute() || !job.is_absolute() ||
        (mode != L"device" && mode != L"frame" && mode != L"sequence")) return 2;
    std::string project;
    for (const auto c : std::wstring_view(argv[4])) {
        if (c > 127) return 2;
        project.push_back(static_cast<char>(c));
    }
    if (!sr::valid_project_id(project) || !std::filesystem::create_directory(job)) return 2;
    std::filesystem::create_directory(job / "logs");
    report_file output{job / "events.jsonl"};
    deadline bound{output};
    probe_report report;
    report.silence_console(); report.observe(&report_file::event, &output);
    try {
        sr::settings settings{640, 360, 960, 540};
        if (dlaa) { settings.input_width = 960; settings.input_height = 540; settings.mode = sr::quality::dlaa; }
        if (rr) settings.auto_exposure = false;
        char input_description[1024]{};
        std::snprintf(input_description, sizeof(input_description),
            "{\"event\":\"probe_input\",\"synthetic_scene\":true,\"feature\":\"%s\",\"mode\":\"%s\","
            "\"input_width\":%u,\"input_height\":%u,\"output_width\":960,\"output_height\":540,"
            "\"color\":\"rgba16f_le\",\"depth\":\"device_z_r32f_le\",\"motion\":\"static_zero_rg16f_le\","
            "\"camera\":\"explicit_static_perspective\",\"frame_time_parameter\":\"not_exposed_by_SL\","
            "\"rr_guides\":%s,\"quality_acceptance\":false}\n",
            rr ? "rr" : "sr", dlaa ? "dlaa" : "quality", settings.input_width, settings.input_height, rr ? "true" : "false");
        output.write(input_description);
        sl_sr_engine engine{settings, report, rr ? sl_reconstruction_feature::ray_reconstruction
                                                : sl_reconstruction_feature::super_resolution};
        engine.initialize(runtime, job / "logs", project);
        if (mode == L"device") engine.present_only();
        else {
            const auto camera = static_camera(settings);
            std::vector<unsigned char> color(sr::input_bytes(settings, 8)), motion(sr::input_bytes(settings, 4));
            std::vector<unsigned char> depth(sr::input_bytes(settings, 4)), rendered(sr::output_bytes(settings));
            std::vector<unsigned char> diffuse, specular, normals;
            if (rr) { diffuse.resize(color.size()); specular.resize(color.size()); normals.resize(color.size()); }
            sl_rr::guides guides{};
            guides.diffuse_albedo = diffuse; guides.specular_albedo = specular;
            guides.normal_roughness = normals; guides.specular_motion = motion;
            for (const auto diagonal : {0U, 5U, 10U, 15U})
                guides.world_to_view[diagonal] = guides.view_to_world[diagonal] = 1;
            for (unsigned int y = 0; y < settings.input_height; ++y) for (unsigned int x = 0; x < settings.input_width; ++x) {
                const std::size_t pixel = static_cast<std::size_t>(y) * settings.input_width + x;
                const bool checker = ((x / 24) ^ (y / 24)) & 1U;
                const std::uint16_t channels[]{static_cast<std::uint16_t>(checker ? 0x3a00 : 0x3000),
                    static_cast<std::uint16_t>(x < settings.input_width / 2 ? 0x3800 : 0x3400),
                    static_cast<std::uint16_t>(y < settings.input_height / 2 ? 0x3400 : 0x3a00), 0x3c00};
                std::memcpy(color.data() + pixel * 8, channels, sizeof(channels));
                constexpr float device_depth = .5F;
                std::memcpy(depth.data() + pixel * 4, &device_depth, sizeof(device_depth));
                if (rr) {
                    // Coherent synthetic material plane: linear diffuse reflectance,
                    // dielectric specular albedo ~0.04, camera-facing unit normals.
                    constexpr std::uint16_t spec[]{0x291f, 0x291f, 0x291f, 0x3c00};
                    constexpr std::uint16_t normal[]{0, 0, 0xbc00, 0x3800};
                    std::memcpy(diffuse.data() + pixel * 8, channels, sizeof(channels));
                    std::memcpy(specular.data() + pixel * 8, spec, sizeof(spec));
                    std::memcpy(normals.data() + pixel * 8, normal, sizeof(normal));
                }
            }
            save(job / "input.rgba16f", color); save(job / "depth.r32f", depth);
            if (rr) {
                save(job / "diffuse.rgba16f", diffuse); save(job / "specular.rgba16f", specular);
                save(job / "normal-roughness.rgba16f", normals); save(job / "specular-motion.rg16f", motion);
            }
            const unsigned int count = mode == L"frame" ? 1 : 8;
            for (unsigned int index = 0; index < count; ++index) {
                sl_sr::frame_info frame{}; frame.reset = index == 0 || index == 4;
                if (rr) {
                    // Four deterministic noise patterns, repeated after reset.
                    // This exercises RR's noisy input route, not ray-traced IQ acceptance.
                    for (std::size_t at = 0; at < color.size(); at += 8) {
                        auto bits = static_cast<std::uint16_t>(diffuse[at] | (static_cast<unsigned int>(diffuse[at + 1]) << 8));
                        bits = static_cast<std::uint16_t>(bits + ((at / 8 + index % 4) % 3) * 32);
                        std::memcpy(color.data() + at, &bits, sizeof(bits));
                    }
                    save(job / ("noisy-input-" + std::to_string(index) + ".rgba16f"), color);
                    engine.evaluate_rr_frame(color, motion, depth, frame, camera, guides, rendered);
                } else engine.evaluate_frame(color, motion, depth, frame, camera, rendered);
                check_output(rendered, index, output);
                save(job / ("output-" + std::to_string(index) + ".rgba16f"), rendered);
            }
        }
        engine.close();
        report.event("probe_complete", "done");
        report.observe(nullptr, nullptr);
        return 0;
    } catch (const std::exception& error) {
        output.error(error.what()); report.event("probe_complete", "failed", 1);
        report.observe(nullptr, nullptr);
        return 1;
    }
}
}

int wmain(int argc, wchar_t** argv) {
    try { return run(argc, argv); } catch (...) { return 2; }
}
