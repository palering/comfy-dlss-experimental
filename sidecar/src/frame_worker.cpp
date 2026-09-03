#include <winsock2.h>
#include <ws2tcpip.h>

#include "frame_worker.hpp"

#include "frame_protocol.hpp"
#include "gpu_frame.hpp"
#include "win32_raii.hpp"

#include <d3d12.h>

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <new>
#include <span>
#include <system_error>
#include <string>
#include <string_view>
#include <vector>

namespace comfy_dlss {
namespace {

namespace wire = protocol;

enum class read_status {
    ok,
    clean_eof,
    error,
};

class byte_transport {
public:
    virtual ~byte_transport() = default;
    virtual std::ptrdiff_t read_some(std::span<std::byte> destination, std::string& error) = 0;
    virtual std::ptrdiff_t write_some(std::span<const std::byte> source, std::string& error) = 0;
    virtual bool flush(std::string& error) = 0;
};

class file_transport final : public byte_transport {
public:
    file_transport(FILE* input, FILE* output) noexcept : input_(input), output_(output) {}

    std::ptrdiff_t read_some(std::span<std::byte> destination, std::string& error) override {
        const std::size_t count = std::fread(destination.data(), 1, destination.size(), input_);
        if (count > 0) return static_cast<std::ptrdiff_t>(count);
        if (std::ferror(input_) != 0) error = "failed to read the frame stream";
        return std::ferror(input_) != 0 ? -1 : 0;
    }

    std::ptrdiff_t write_some(std::span<const std::byte> source, std::string& error) override {
        const std::size_t count = std::fwrite(source.data(), 1, source.size(), output_);
        if (count > 0) return static_cast<std::ptrdiff_t>(count);
        error = "failed to write the frame stream";
        return -1;
    }

    bool flush(std::string& error) override {
        if (std::fflush(output_) == 0) return true;
        error = "failed to flush the frame stream";
        return false;
    }

private:
    FILE* input_;
    FILE* output_;
};

class socket_transport final : public byte_transport {
public:
    explicit socket_transport(SOCKET socket) noexcept : socket_(socket) {}
    ~socket_transport() override {
        if (socket_ != INVALID_SOCKET) closesocket(socket_);
    }
    socket_transport(const socket_transport&) = delete;
    socket_transport& operator=(const socket_transport&) = delete;

    std::ptrdiff_t read_some(std::span<std::byte> destination, std::string& error) override {
        const std::size_t bounded = std::min(
            destination.size(), static_cast<std::size_t>(std::numeric_limits<int>::max()));
        const int count = recv(socket_, reinterpret_cast<char*>(destination.data()), static_cast<int>(bounded), 0);
        if (count >= 0) return count;
        error = "recv failed with Winsock error " + std::to_string(WSAGetLastError());
        return -1;
    }

    std::ptrdiff_t write_some(std::span<const std::byte> source, std::string& error) override {
        const std::size_t bounded = std::min(
            source.size(), static_cast<std::size_t>(std::numeric_limits<int>::max()));
        const int count = send(socket_, reinterpret_cast<const char*>(source.data()), static_cast<int>(bounded), 0);
        if (count > 0) return count;
        error = "send failed with Winsock error " + std::to_string(WSAGetLastError());
        return -1;
    }

    bool flush(std::string&) override {
        return true;
    }

private:
    SOCKET socket_ = INVALID_SOCKET;
};

class winsock_session final {
public:
    bool start(std::string& error) {
        WSADATA data{};
        const int result = WSAStartup(MAKEWORD(2, 2), &data);
        if (result != 0) {
            error = "WSAStartup failed with error " + std::to_string(result);
            return false;
        }
        started_ = true;
        if (LOBYTE(data.wVersion) != 2 || HIBYTE(data.wVersion) != 2) {
            error = "Winsock 2.2 is unavailable";
            return false;
        }
        return true;
    }

    ~winsock_session() {
        if (started_) WSACleanup();
    }
    winsock_session(const winsock_session&) = delete;
    winsock_session& operator=(const winsock_session&) = delete;
    winsock_session() noexcept = default;

private:
    bool started_ = false;
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
                if (character < 0x20U) {
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

read_status read_exact(byte_transport& transport, std::span<std::byte> destination, std::string& error) {
    std::size_t completed = 0;
    while (completed < destination.size()) {
        const std::ptrdiff_t count = transport.read_some(destination.subspan(completed), error);
        if (count > 0) {
            completed += static_cast<std::size_t>(count);
            continue;
        }
        if (count < 0) return read_status::error;
        if (completed == 0) return read_status::clean_eof;
        error = "frame stream ended in the middle of a packet";
        return read_status::error;
    }
    return read_status::ok;
}

void append_u16(std::vector<std::byte>& bytes, std::uint16_t value) {
    for (unsigned int shift = 0; shift < 16U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffU));
    }
}

void append_u32(std::vector<std::byte>& bytes, std::uint32_t value) {
    for (unsigned int shift = 0; shift < 32U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffU));
    }
}

void append_u64(std::vector<std::byte>& bytes, std::uint64_t value) {
    for (unsigned int shift = 0; shift < 64U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffULL));
    }
}

bool write_bytes(byte_transport& transport, std::span<const std::byte> bytes, std::string& error) {
    std::size_t completed = 0;
    while (completed < bytes.size()) {
        const std::ptrdiff_t count = transport.write_some(bytes.subspan(completed), error);
        if (count <= 0) return false;
        completed += static_cast<std::size_t>(count);
    }
    return true;
}

bool write_packet(
    byte_transport& transport,
    wire::message_type type,
    wire::packet_flags flags,
    std::uint64_t request_id,
    std::span<const std::byte> payload,
    std::string& error) {
    if (payload.size() > wire::max_packet_bytes) {
        error = "attempted to write an oversized frame packet";
        return false;
    }
    std::vector<std::byte> header;
    header.reserve(wire::packet_header_bytes);
    header.insert(header.end(), wire::magic.begin(), wire::magic.end());
    append_u16(header, wire::version);
    append_u16(header, static_cast<std::uint16_t>(type));
    append_u32(header, static_cast<std::uint32_t>(flags));
    append_u64(header, request_id);
    append_u64(header, static_cast<std::uint64_t>(payload.size()));
    if (header.size() != wire::packet_header_bytes ||
        !write_bytes(transport, header, error) ||
        !write_bytes(transport, payload, error)) {
        return false;
    }
    return transport.flush(error);
}

bool write_json_packet(
    byte_transport& transport,
    wire::message_type type,
    std::uint64_t request_id,
    std::string_view json,
    std::string& error) {
    const auto bytes = std::as_bytes(std::span(json.data(), json.size()));
    return write_packet(transport, type, wire::packet_flags::none, request_id, bytes, error);
}

bool write_error_packet(
    byte_transport& transport,
    std::uint64_t request_id,
    std::string_view stage,
    std::string_view message,
    std::string& write_error) {
    const std::string payload = "{\"stage\":\"" + json_escape(stage) +
        "\",\"error\":\"" + json_escape(message) + "\"}";
    return write_json_packet(transport, wire::message_type::error, request_id, payload, write_error);
}

read_status read_packet(
    byte_transport& transport,
    wire::packet_header& header,
    std::vector<std::byte>& payload,
    std::string& error) {
    std::array<std::byte, wire::packet_header_bytes> header_bytes{};
    const read_status status = read_exact(transport, header_bytes, error);
    if (status != read_status::ok) return status;
    if (!wire::parse_packet_header(header_bytes, header, error)) return read_status::error;
    if (header.payload_bytes > std::numeric_limits<std::size_t>::max()) {
        error = "packet size cannot be represented by this worker";
        return read_status::error;
    }
    try {
        payload.assign(static_cast<std::size_t>(header.payload_bytes), std::byte{0});
    } catch (const std::bad_alloc&) {
        error = "frame packet allocation failed";
        return read_status::error;
    }
    if (payload.empty()) return read_status::ok;
    const auto body_status = read_exact(transport, payload, error);
    if (body_status == read_status::clean_eof) {
        error = "frame stream ended before its declared packet body";
        return read_status::error;
    }
    return body_status;
}

bool make_frame_result(
    const wire::frame_description& frame,
    const std::vector<std::vector<std::byte>>& planes,
    std::vector<std::byte>& payload,
    std::string& error) {
    if (planes.size() != frame.planes.size()) {
        error = "GPU result plane count mismatch";
        return false;
    }
    std::uint64_t total = wire::frame_header_bytes + planes.size() * wire::plane_header_bytes;
    for (std::size_t index = 0; index < planes.size(); ++index) {
        const auto& plane = frame.planes[index];
        const std::uint64_t pitch = static_cast<std::uint64_t>(plane.width) * wire::bytes_per_pixel(plane.format);
        if (planes[index].size() != pitch * plane.height || planes[index].size() > wire::max_packet_bytes - total) {
            error = "invalid GPU result byte count or packet limit exceeded";
            return false;
        }
        total += planes[index].size();
    }
    payload.clear();
    payload.reserve(static_cast<std::size_t>(total));
    append_u32(payload, frame.input_width);
    append_u32(payload, frame.input_height);
    append_u32(payload, frame.output_width);
    append_u32(payload, frame.output_height);
    append_u64(payload, std::bit_cast<std::uint64_t>(frame.pts_ns));
    append_u64(payload, frame.frame_index);
    append_u32(payload, static_cast<std::uint32_t>(planes.size()));
    append_u32(payload, 0);
    for (std::size_t index = 0; index < planes.size(); ++index) {
        const auto& plane = frame.planes[index];
        const auto semantic = plane.semantic == wire::plane_semantic::color
            ? wire::plane_semantic::output_color : plane.semantic;
        append_u16(payload, static_cast<std::uint16_t>(semantic));
        append_u16(payload, static_cast<std::uint16_t>(plane.format));
        append_u32(payload, plane.width);
        append_u32(payload, plane.height);
        append_u32(payload, plane.width * wire::bytes_per_pixel(plane.format));
        append_u32(payload, 0);
        append_u32(payload, 0);
        append_u64(payload, planes[index].size());
    }
    for (const auto& bytes : planes) payload.insert(payload.end(), bytes.begin(), bytes.end());
    return true;
}

bool process_frame(
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::span<const std::byte> payload,
    std::vector<std::byte>& result_payload,
    std::string& error) {
    try {
        wire::frame_description frame{};
        if (!wire::parse_frame_payload(payload, frame, error) ||
            !wire::validate_frame_inputs(frame, error)) return false;
        if (frame.output_width != frame.input_width || frame.output_height != frame.input_height) {
            error = "passthrough milestone requires equal input and output dimensions";
            return false;
        }
        std::vector<std::vector<std::byte>> planes;
        if (!gpu_frame_round_trip(device, queue, frame, payload, planes, error)) return false;
        return make_frame_result(frame, planes, result_payload, error);
    } catch (const std::bad_alloc&) {
        error = "frame resource allocation failed";
        return false;
    }
}

bool run_frame_worker_transport(
    byte_transport& transport,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::string& error) {
    for (;;) {
        wire::packet_header header{};
        std::vector<std::byte> payload;
        const read_status status = read_packet(transport, header, payload, error);
        if (status == read_status::clean_eof) return true;
        if (status == read_status::error) return false;

        switch (header.type) {
            case wire::message_type::hello:
                if (!write_json_packet(
                        transport,
                        wire::message_type::hello,
                        header.request_id,
                        "{\"protocol\":\"comfy-dlss-sidecar/1\",\"renderer\":\"d3d12-passthrough\","
                        "\"formats\":[\"RGBA8_UNORM\",\"RGBA16_FLOAT\",\"RG16_FLOAT\",\"R32_FLOAT\",\"R8_UNORM\"],"
                        "\"guide_readback\":true,\"configuration_supported\":false,\"ngx\":false}",
                        error)) {
                    return false;
                }
                break;
            case wire::message_type::configure:
                if (payload.size() > 64U * 1024U) {
                    const std::string message = "CONFIGURE payload exceeds 64 KiB";
                    if (!write_error_packet(transport, header.request_id, "configure", message, error)) return false;
                    error = message;
                    return false;
                }
                if (!write_json_packet(
                        transport,
                        wire::message_type::configured,
                        header.request_id,
                        "{\"accepted\":false,\"renderer\":\"d3d12-passthrough\","
                        "\"reason\":\"diagnostic copy worker does not apply configuration\","
                        "\"output_format\":\"same-as-input\"}",
                        error)) {
                    return false;
                }
                break;
            case wire::message_type::frame: {
                std::vector<std::byte> result_payload;
                std::string frame_error;
                if (!process_frame(device, queue, payload, result_payload, frame_error)) {
                    if (!write_error_packet(transport, header.request_id, "frame", frame_error, error)) return false;
                    error = frame_error;
                    return false;
                }
                if (!write_packet(
                        transport,
                        wire::message_type::frame_result,
                        header.flags,
                        header.request_id,
                        result_payload,
                        error)) {
                    return false;
                }
                break;
            }
            case wire::message_type::reset:
                if (!write_json_packet(
                        transport, wire::message_type::reset, header.request_id,
                        "{\"reset\":true}", error)) {
                    return false;
                }
                break;
            case wire::message_type::cancel:
                return write_json_packet(
                    transport, wire::message_type::cancel, header.request_id,
                    "{\"cancelled\":true}", error);
            case wire::message_type::shutdown:
                return write_json_packet(
                    transport, wire::message_type::shutdown, header.request_id,
                    "{\"shutdown\":true}", error);
            case wire::message_type::configured:
            case wire::message_type::frame_result:
            case wire::message_type::error: {
                const std::string message = "worker received a response-only message type";
                if (!write_error_packet(transport, header.request_id, "protocol", message, error)) return false;
                error = message;
                return false;
            }
        }
    }
}

bool decode_token(std::string_view encoded, std::array<std::byte, 32>& token, std::string& error) {
    if (encoded.size() != token.size() * 2U) {
        error = "TCP authentication token must contain exactly 64 hexadecimal characters";
        return false;
    }
    const auto digit = [](char character) -> int {
        if (character >= '0' && character <= '9') return character - '0';
        if (character >= 'a' && character <= 'f') return character - 'a' + 10;
        if (character >= 'A' && character <= 'F') return character - 'A' + 10;
        return -1;
    };
    for (std::size_t index = 0; index < token.size(); ++index) {
        const int high = digit(encoded[index * 2U]);
        const int low = digit(encoded[index * 2U + 1U]);
        if (high < 0 || low < 0) {
            error = "TCP authentication token contains a non-hexadecimal character";
            return false;
        }
        token[index] = static_cast<std::byte>(static_cast<unsigned int>((high << 4) | low));
    }
    return true;
}

}  // namespace

bool run_frame_worker(
    FILE* input,
    FILE* output,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::string& error) {
    if (input == nullptr || output == nullptr || device == nullptr || queue == nullptr) {
        error = "frame worker received a null stream or D3D12 object";
        return false;
    }
    file_transport transport{input, output};
    return run_frame_worker_transport(transport, device, queue, error);
}

bool run_frame_worker_tcp(
    std::string_view host,
    std::uint16_t port,
    std::string_view authentication_token_hex,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::string& error) {
    if (host != "127.0.0.1" || port == 0 || device == nullptr || queue == nullptr) {
        error = "TCP frame worker requires 127.0.0.1, a non-zero port, and valid D3D12 objects";
        return false;
    }
    std::array<std::byte, 32> token{};
    if (!decode_token(authentication_token_hex, token, error)) return false;
    winsock_session session;
    if (!session.start(error)) return false;

    const SOCKET raw_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (raw_socket == INVALID_SOCKET) {
        error = "socket failed with Winsock error " + std::to_string(WSAGetLastError());
        return false;
    }
    socket_transport transport{raw_socket};
    DWORD timeout_ms = 120'000;
    if (setsockopt(
            raw_socket, SOL_SOCKET, SO_RCVTIMEO,
            reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)) == SOCKET_ERROR ||
        setsockopt(
            raw_socket, SOL_SOCKET, SO_SNDTIMEO,
            reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)) == SOCKET_ERROR) {
        error = "setsockopt timeout failed with Winsock error " + std::to_string(WSAGetLastError());
        return false;
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (inet_pton(AF_INET, "127.0.0.1", &address.sin_addr) != 1) {
        error = "inet_pton rejected the loopback address";
        return false;
    }
    if (connect(raw_socket, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
        error = "connect to the local frame server failed with Winsock error " +
            std::to_string(WSAGetLastError());
        return false;
    }
    if (!write_bytes(transport, token, error) || !transport.flush(error)) return false;
    return run_frame_worker_transport(transport, device, queue, error);
}

}  // namespace comfy_dlss
