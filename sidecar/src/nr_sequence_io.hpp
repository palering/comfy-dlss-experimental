#pragma once
#include "nr_sequence_contract.hpp"
#include "win32_raii.hpp"
#include <array>
#include <filesystem>
#include <vector>

namespace comfy_dlss::sequence_probe {
inline void read_exact(HANDLE file, std::span<unsigned char> bytes) {
    while (!bytes.empty()) {
        DWORD count = 0;
        const auto chunk = static_cast<DWORD>(bytes.size() > 1024*1024 ? 1024*1024 : bytes.size());
        if (!ReadFile(file, bytes.data(), chunk, &count, nullptr) || !count)
            throw std::runtime_error("Sequence file read failed");
        bytes = bytes.subspan(count);
    }
}
inline void write_exact(HANDLE file, std::span<const unsigned char> bytes) {
    while (!bytes.empty()) {
        DWORD count = 0;
        const auto chunk = static_cast<DWORD>(bytes.size() > 1024*1024 ? 1024*1024 : bytes.size());
        if (!WriteFile(file, bytes.data(), chunk, &count, nullptr) || !count)
            throw std::runtime_error("Sequence output write failed");
        bytes = bytes.subspan(count);
    }
}
class input final {
    unique_handle file_;
    std::uint32_t index_ = 0;
    std::uint64_t previous_pts_ = 0;
public:
    layout shape{};
    std::vector<unsigned char> color, motion;
    explicit input(const std::filesystem::path& path) {
        if (!path.is_absolute()) throw std::runtime_error("Sequence input must be absolute");
        // Keep one read-only, non-write-shared handle throughout preflight and GPU run.
        file_.reset(CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
        LARGE_INTEGER size{};
        if (!file_ || GetFileType(file_.get()) != FILE_TYPE_DISK || !GetFileSizeEx(file_.get(), &size) || size.QuadPart < 0)
            throw std::runtime_error("Invalid sequence input file");
        std::array<unsigned char, header_bytes> header{}; read_exact(file_.get(), header);
        shape = parse_layout(header, static_cast<std::uint64_t>(size.QuadPart));
        color.resize(shape.color_bytes); motion.resize(shape.motion_bytes);
        // Preflight all records with O(one frame) memory, before any GPU call.
        for (std::uint32_t i = 0; i < shape.count; ++i) next();
        LARGE_INTEGER offset{}; offset.QuadPart = header_bytes;
        if (!SetFilePointerEx(file_.get(), offset, nullptr, FILE_BEGIN)) throw std::runtime_error("Sequence rewind failed");
        index_ = 0; previous_pts_ = 0;
    }
    frame_info next() {
        if (index_ >= shape.count) throw std::runtime_error("Sequence exhausted");
        std::array<unsigned char, frame_header_bytes> header{}; read_exact(file_.get(), header);
        auto frame = parse_frame(header, index_, previous_pts_);
        read_exact(file_.get(), color); read_exact(file_.get(), motion);
        if (!finite_half_plane(color) || !finite_half_plane(motion)) throw std::runtime_error("Non-finite sequence input");
        previous_pts_ = frame.pts; ++index_; return frame;
    }
};
class output final {
    unique_handle file_;
public:
    explicit output(const std::filesystem::path& path) {
        if (!path.is_absolute()) throw std::runtime_error("Sequence output must be absolute");
        file_.reset(CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!file_ || GetFileType(file_.get()) != FILE_TYPE_DISK) throw std::runtime_error("Output must be a new regular file");
    }
    void append(std::span<const unsigned char> bytes) { write_exact(file_.get(), bytes); }
    void finish() { if (!FlushFileBuffers(file_.get())) throw std::runtime_error("Output flush failed"); }
};
}
