#pragma once

// This header must precede Windows/NGX headers. Diagnostic protocol only: no
// video packets or compatibility changes to the legacy relay protocol.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string_view>

namespace comfy_dlss {
class probe_report final {
public:
    using event_observer = void (*)(void*, const char*, const char*, unsigned long) noexcept;
private:
    struct winsock final {
        winsock() {
            WSADATA data{};
            if (WSAStartup(MAKEWORD(2, 2), &data)) throw std::runtime_error("Report WSAStartup failed");
        }
        ~winsock() { WSACleanup(); }
    } startup_;
    struct connection final {
        SOCKET value = INVALID_SOCKET;
        ~connection() { if (value != INVALID_SOCKET) closesocket(value); }
    } socket_;
    unsigned int sequence_ = 0;
    bool silent_ = false;
    event_observer observer_ = nullptr;
    void* observer_context_ = nullptr;

    bool send_bytes(const char* bytes, int size) noexcept {
        while (size > 0) {
            const int n = send(socket_.value, bytes, size, 0);
            if (n <= 0) return false;
            bytes += n; size -= n;
        }
        return true;
    }
    bool acknowledged() noexcept {
        char value = 0;
        return recv(socket_.value, &value, 1, 0) == 1 && value == 1;
    }
public:
    probe_report() = default;
    probe_report(const probe_report&) = delete;
    probe_report& operator=(const probe_report&) = delete;
    bool connected() const noexcept { return socket_.value != INVALID_SOCKET; }
    bool console_enabled() const noexcept { return !silent_ && !connected(); }
    void silence_console() noexcept { silent_ = true; }
    // Observer is non-owning, synchronous and allocation-free. A scoped owner
    // must detach it before its context dies; diagnostic failure cannot unwind
    // through native GPU owners or alter existing fence termination policy.
    void observe(event_observer observer, void* context) noexcept {
        observer_ = observer; observer_context_ = context;
    }
    void connect_local(unsigned short port, std::wstring_view hex) {
        if (socket_.value != INVALID_SOCKET || port == 0 || hex.size() != 64)
            throw std::runtime_error("Invalid report endpoint");
        std::array<char, 32> token{};
        auto digit = [](wchar_t c) -> unsigned int {
            if (c >= L'0' && c <= L'9') return static_cast<unsigned int>(c - L'0');
            if (c >= L'a' && c <= L'f') return static_cast<unsigned int>(c - L'a') + 10;
            throw std::runtime_error("Invalid report token");
        };
        for (std::size_t i = 0; i < token.size(); ++i) {
            const auto value = static_cast<unsigned char>((digit(hex[2*i]) << 4) | digit(hex[2*i+1]));
            std::memcpy(&token[i], &value, 1);
        }
        socket_.value = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (socket_.value == INVALID_SOCKET) throw std::runtime_error("Report socket failed");
        const DWORD timeout_ms = 3000;
        if (setsockopt(socket_.value, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)) ||
            setsockopt(socket_.value, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)))
            throw std::runtime_error("Report socket timeout failed");
        // Nonblocking connect with an explicit deadline, including loopback failure.
        u_long nonblocking = 1;
        if (ioctlsocket(socket_.value, static_cast<long>(FIONBIO), &nonblocking)) throw std::runtime_error("Report socket mode failed");
        sockaddr_in address{}; address.sin_family = AF_INET;
        address.sin_port = htons(port); address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect(socket_.value, reinterpret_cast<const sockaddr*>(&address), sizeof(address))) {
            if (WSAGetLastError() != WSAEWOULDBLOCK) throw std::runtime_error("Report connect failed");
            fd_set writable, failed; FD_ZERO(&writable); FD_ZERO(&failed);
            FD_SET(socket_.value, &writable); FD_SET(socket_.value, &failed);
            timeval timeout{3, 0};
            if (select(0, nullptr, &writable, &failed, &timeout) <= 0 || FD_ISSET(socket_.value, &failed))
                throw std::runtime_error("Report connect timeout/failure");
            int error = 0, size = sizeof(error);
            if (getsockopt(socket_.value, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&error), &size) || error)
                throw std::runtime_error("Report connect error");
        }
        nonblocking = 0;
        if (ioctlsocket(socket_.value, static_cast<long>(FIONBIO), &nonblocking) ||
            !send_bytes(token.data(), static_cast<int>(token.size())) || !acknowledged())
            throw std::runtime_error("Report authentication failed");
    }
    // All callers supply fixed ASCII literals, not paths/model/user strings.
    // No allocation or exception; a broken diagnostic channel terminates only
    // this process rather than unwinding potentially in-flight GPU owners.
    void event(const char* stage, const char* state, unsigned long code = 0) noexcept {
        if (observer_) observer_(observer_context_, stage, state, code);
        char buffer[256]{};
        const int size = std::snprintf(buffer, sizeof(buffer),
            "{\"protocol\":1,\"sequence\":%u,\"stage\":\"%s\",\"state\":\"%s\",\"code\":%lu}\n",
            sequence_++, stage, state, code);
        if (size <= 0 || size >= static_cast<int>(sizeof(buffer))) std::_Exit(74);
        if (socket_.value != INVALID_SOCKET) {
            if (!send_bytes(buffer, size)) std::_Exit(74);
        } else if (!silent_) {
            std::fputs(buffer, stderr);
        }
    }
    void complete(unsigned long code) noexcept {
        event("process", "complete", code);
        if (socket_.value != INVALID_SOCKET && !acknowledged()) std::_Exit(74);
    }
};
} // namespace comfy_dlss
