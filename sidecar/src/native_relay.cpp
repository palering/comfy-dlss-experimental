#include <winsock2.h>
#include <ws2tcpip.h>

#include "win32_raii.hpp"
#include "relay_protocol.hpp"

#include <array>
#include <atomic>
#include <cstdio>
#include <limits>
#include <mutex>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
using comfy_dlss::unique_handle;
namespace wire = comfy_dlss::relay_wire;

[[noreturn]] void win_error(const char* stage) {
    throw std::runtime_error(std::string(stage) + " Win32=" + std::to_string(GetLastError()));
}

struct winsock final {
    winsock() {
        WSADATA data{};
        const int result = WSAStartup(MAKEWORD(2, 2), &data);
        if (result != 0) throw std::runtime_error("WSAStartup=" + std::to_string(result));
    }
    ~winsock() { WSACleanup(); }
    winsock(const winsock&) = delete;
    winsock& operator=(const winsock&) = delete;
};

struct connection final {
    SOCKET value = INVALID_SOCKET;
    ~connection() { if (value != INVALID_SOCKET) closesocket(value); }
    connection() = default;
    connection(const connection&) = delete;
    connection& operator=(const connection&) = delete;
};

bool send_all(SOCKET socket, std::span<const std::byte> bytes) {
    while (!bytes.empty()) {
        const int count = send(socket, reinterpret_cast<const char*>(bytes.data()),
                               static_cast<int>(bytes.size()), 0);
        if (count <= 0) return false;
        bytes = bytes.subspan(static_cast<std::size_t>(count));
    }
    return true;
}

bool receive_all(SOCKET socket, std::span<std::byte> bytes) {
    while (!bytes.empty()) {
        const int count = recv(socket, reinterpret_cast<char*>(bytes.data()), static_cast<int>(bytes.size()), 0);
        if (count <= 0) return false;
        bytes = bytes.subspan(static_cast<std::size_t>(count));
    }
    return true;
}

struct relay_state final {
    SOCKET socket = INVALID_SOCKET; // owned by connection, outlives all threads
    std::mutex writer;
    std::atomic<bool> stop{false};
    std::atomic<bool> finishing{false};
    std::atomic<bool> io_error{false};
    unique_handle input;
    unique_handle output;
    unique_handle diagnostic;

    bool send_packet(wire::kind type, std::span<const std::byte> bytes) {
        const auto head = wire::header(type, static_cast<std::uint32_t>(bytes.size()));
        std::lock_guard<std::mutex> lock(writer);
        if (send_all(socket, head) && send_all(socket, bytes)) return true;
        io_error = true;
        stop = true;
        shutdown(socket, SD_BOTH);
        return false;
    }
};

DWORD WINAPI input_thread(void* opaque) noexcept {
    auto& state = *static_cast<relay_state*>(opaque);
    try {
        std::array<std::byte, wire::max_payload> bytes{};
        while (!state.finishing.load()) {
            std::array<std::byte, wire::header_size> raw{};
            if (!receive_all(state.socket, raw)) {
                if (!state.finishing.load()) state.stop = true;
                break;
            }
            wire::kind type{};
            std::uint32_t size = 0;
            if (!wire::parse(raw, type, size)) throw std::runtime_error("invalid relay input header");
            if ((type == wire::kind::input_end || type == wire::kind::cancel) && size != 0)
                throw std::runtime_error("control payload must be empty");
            if (type == wire::kind::cancel) { state.stop = true; break; }
            if (type == wire::kind::input_end) {
                state.input.reset();
                // Keep receiving CANCEL/disconnect while the worker drains its output.
                continue;
            }
            if (type != wire::kind::input || !state.input || size == 0)
                throw std::runtime_error("unexpected relay input packet");
            auto remaining = std::span(bytes).first(size);
            if (!receive_all(state.socket, remaining)) { state.stop = true; break; }
            while (!remaining.empty()) {
                DWORD written = 0;
                if (!WriteFile(state.input.get(), remaining.data(), static_cast<DWORD>(remaining.size()), &written, nullptr)
                    || written == 0) {
                    if (!state.finishing.load()) { state.io_error = true; state.stop = true; }
                    state.input.reset();
                    return 0;
                }
                remaining = remaining.subspan(written);
            }
        }
    } catch (...) { state.io_error = true; state.stop = true; }
    state.input.reset(); // the input thread is the only owner after it starts
    return 0;
}

struct reader_context final { relay_state* state; HANDLE pipe; wire::kind type; };
DWORD WINAPI output_thread(void* opaque) noexcept {
    const auto context = *static_cast<reader_context*>(opaque);
    try {
        std::array<std::byte, wire::max_payload> bytes{};
        for (;;) {
            DWORD count = 0;
            if (!ReadFile(context.pipe, bytes.data(), static_cast<DWORD>(bytes.size()), &count, nullptr)) {
                const DWORD error = GetLastError();
                if (error != ERROR_BROKEN_PIPE && error != ERROR_OPERATION_ABORTED) {
                    context.state->io_error = true;
                    context.state->stop = true;
                }
                break;
            }
            if (count == 0 || !context.state->send_packet(context.type, std::span(bytes).first(count))) break;
        }
    } catch (...) { context.state->io_error = true; context.state->stop = true; }
    return 0;
}

// Closing the job kills its entire Windows process tree, including on exceptions.
struct child_process final {
    unique_handle job;
    unique_handle process;
    unique_handle primary_thread;
    DWORD pid = 0;
    ~child_process() {
        if (job) TerminateJobObject(job.get(), 125);
        if (process) {
            // Also contains the suspended child if AssignProcessToJobObject failed.
            TerminateProcess(process.get(), 125);
            WaitForSingleObject(process.get(), 5000);
        }
    }
};

struct attributes final {
    std::vector<std::byte> storage;
    LPPROC_THREAD_ATTRIBUTE_LIST list = nullptr;
    attributes() {
        SIZE_T size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
        if (size == 0) win_error("attribute list size");
        storage.resize(size);
        list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
        if (!InitializeProcThreadAttributeList(list, 1, 0, &size)) { list = nullptr; win_error("attribute list init"); }
    }
    ~attributes() { if (list) DeleteProcThreadAttributeList(list); }
    attributes(const attributes&) = delete;
    attributes& operator=(const attributes&) = delete;
};

void make_pipe(unique_handle& read_end, unique_handle& write_end, bool parent_reads) {
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    HANDLE read_raw = nullptr, write_raw = nullptr;
    if (!CreatePipe(&read_raw, &write_raw, &security, wire::max_payload)) win_error("CreatePipe");
    read_end.reset(read_raw);
    write_end.reset(write_raw);
    if (!SetHandleInformation(parent_reads ? read_raw : write_raw, HANDLE_FLAG_INHERIT, 0))
        win_error("SetHandleInformation");
}

std::wstring full_path(const std::wstring& path) {
    std::vector<wchar_t> buffer(32768);
    const DWORD count = GetFullPathNameW(path.c_str(), static_cast<DWORD>(buffer.size()), buffer.data(), nullptr);
    if (count == 0 || count >= buffer.size()) throw std::runtime_error("invalid worker path");
    return std::wstring(buffer.data(), count);
}

void launch_child(child_process& child, relay_state& state, const std::wstring& raw_path) {
    const auto path = full_path(raw_path);
    if (path.find(L'"') != std::wstring::npos) throw std::runtime_error("quote in worker path");
    const DWORD flags = GetFileAttributesW(path.c_str());
    if (flags == INVALID_FILE_ATTRIBUTES || (flags & FILE_ATTRIBUTE_DIRECTORY)) win_error("worker file");
    const auto slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) throw std::runtime_error("worker path has no parent");
    const auto directory = path.substr(0, slash);
    unique_handle child_in, child_out, child_err;
    make_pipe(child_in, state.input, false);
    make_pipe(state.output, child_out, true);
    make_pipe(state.diagnostic, child_err, true);

    child.job.reset(CreateJobObjectW(nullptr, nullptr));
    if (!child.job) win_error("CreateJobObject");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(child.job.get(), JobObjectExtendedLimitInformation, &limits, sizeof(limits)))
        win_error("SetInformationJobObject");
    attributes attrs;
    std::array<HANDLE, 3> inherited{child_in.get(), child_out.get(), child_err.get()};
    if (!UpdateProcThreadAttribute(attrs.list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                  inherited.data(), sizeof(inherited), nullptr, nullptr)) win_error("inherit handle list");
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = child_in.get();
    startup.StartupInfo.hStdOutput = child_out.get();
    startup.StartupInfo.hStdError = child_err.get();
    startup.lpAttributeList = attrs.list;
    std::wstring command = L"\"" + path + L"\" --video";
    PROCESS_INFORMATION information{};
    if (!CreateProcessW(path.c_str(), command.data(), nullptr, nullptr, TRUE,
                        CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
                        nullptr, directory.c_str(), &startup.StartupInfo, &information)) win_error("CreateProcessW");
    child.process.reset(information.hProcess);
    child.primary_thread.reset(information.hThread);
    child.pid = information.dwProcessId;
    if (!AssignProcessToJobObject(child.job.get(), child.process.get())) win_error("AssignProcessToJobObject");
    if (ResumeThread(child.primary_thread.get()) == static_cast<DWORD>(-1)) win_error("ResumeThread");
}

// No thread may outlive its contexts/pipe handles. A pathological cancellation
// failure exits the isolated relay instead of destructing memory still in use.
struct threads final {
    relay_state& state;
    child_process& child;
    std::array<unique_handle, 3> handles;
    reader_context out_context;
    reader_context err_context;
    bool joined = false;
    threads(relay_state& s, child_process& c)
        : state(s), child(c), out_context{&s, s.output.get(), wire::kind::output},
          err_context{&s, s.diagnostic.get(), wire::kind::diagnostic} {}
    void start() {
        handles[0].reset(CreateThread(nullptr, 0, input_thread, &state, 0, nullptr));
        if (!handles[0]) win_error("input thread");
        handles[1].reset(CreateThread(nullptr, 0, output_thread, &out_context, 0, nullptr));
        if (!handles[1]) win_error("output thread");
        handles[2].reset(CreateThread(nullptr, 0, output_thread, &err_context, 0, nullptr));
        if (!handles[2]) win_error("diagnostic thread");
    }
    void join(bool graceful) noexcept {
        if (joined) return;
        state.finishing = true;
        if (child.job) TerminateJobObject(child.job.get(), 125);
        shutdown(state.socket, SD_RECEIVE);
        if (handles[0]) CancelSynchronousIo(handles[0].get());
        if (graceful) {
            for (std::size_t i = 1; i < handles.size(); ++i)
                if (handles[i] && WaitForSingleObject(handles[i].get(), 5000) != WAIT_OBJECT_0) {
                    state.io_error = true;
                    break;
                }
        }
        if (!graceful || state.io_error.load()) shutdown(state.socket, SD_BOTH);
        for (auto& thread : handles) if (thread) {
            CancelSynchronousIo(thread.get());
            if (WaitForSingleObject(thread.get(), 12000) != WAIT_OBJECT_0) {
                // ExitProcess closes the non-inherited job handle and kills the child.
                ExitProcess(70);
            }
        }
        joined = true;
    }
    ~threads() { join(false); }
};

unsigned parse_number(const std::wstring& value, unsigned minimum, unsigned maximum) {
    if (value.empty()) throw std::runtime_error("missing number");
    unsigned result = 0;
    for (const wchar_t ch : value) {
        if (ch < L'0' || ch > L'9') throw std::runtime_error("invalid number");
        const auto digit = static_cast<unsigned>(ch - L'0');
        if (result > (maximum - digit) / 10U) throw std::runtime_error("number out of range");
        result = result * 10U + digit;
    }
    if (result < minimum || result > maximum) throw std::runtime_error("number out of range");
    return result;
}

std::array<std::byte, 32> token_bytes(const std::wstring& raw) {
    if (raw.size() != 64) throw std::runtime_error("token must be 64 hex characters");
    auto digit = [](wchar_t ch) -> unsigned {
        if (ch >= L'0' && ch <= L'9') return static_cast<unsigned>(ch - L'0');
        if (ch >= L'a' && ch <= L'f') return static_cast<unsigned>(ch - L'a') + 10U;
        throw std::runtime_error("invalid token");
    };
    std::array<std::byte, 32> result{};
    for (std::size_t i = 0; i < result.size(); ++i)
        result[i] = static_cast<std::byte>(digit(raw[i * 2]) * 16U + digit(raw[i * 2 + 1]));
    return result;
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc != 9 || std::wstring_view(argv[1]) != L"--worker" || std::wstring_view(argv[3]) != L"--port"
            || std::wstring_view(argv[5]) != L"--token" || std::wstring_view(argv[7]) != L"--timeout-ms")
            throw std::runtime_error("usage: relay --worker ABS_PATH --port PORT --token HEX --timeout-ms MS");
        const auto port = parse_number(argv[4], 1, 65535);
        const auto token = token_bytes(argv[6]);
        const auto timeout = parse_number(argv[8], 1000, 3600000);
        winsock runtime;
        connection channel;
        channel.value = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_NO_HANDLE_INHERIT);
        if (channel.value == INVALID_SOCKET) throw std::runtime_error("WSASocket failed");
        // Output packets include small headers. With request/response frame
        // traffic, Nagle plus delayed ACK otherwise stalls the tail of a frame.
        // Set this on both peers; disabling it on Python alone is insufficient.
        const BOOL no_delay = TRUE;
        if (setsockopt(channel.value, IPPROTO_TCP, TCP_NODELAY,
                       reinterpret_cast<const char*>(&no_delay), sizeof(no_delay)))
            throw std::runtime_error("TCP_NODELAY failed=" + std::to_string(WSAGetLastError()));
        const DWORD socket_timeout = 10000;
        for (const int option : {SO_RCVTIMEO, SO_SNDTIMEO})
            if (setsockopt(channel.value, SOL_SOCKET, option, reinterpret_cast<const char*>(&socket_timeout), sizeof(socket_timeout)))
                throw std::runtime_error("socket timeout failed");
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<u_short>(port));
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect(channel.value, reinterpret_cast<const sockaddr*>(&address), sizeof(address)))
            throw std::runtime_error("connect failed=" + std::to_string(WSAGetLastError()));
        std::array<std::byte, 1> ack{};
        if (!send_all(channel.value, token) || !receive_all(channel.value, ack) || ack[0] != std::byte{1})
            throw std::runtime_error("relay authentication failed");
        const DWORD no_receive_timeout = 0;
        if (setsockopt(channel.value, SOL_SOCKET, SO_RCVTIMEO,
                       reinterpret_cast<const char*>(&no_receive_timeout), sizeof(no_receive_timeout)))
            throw std::runtime_error("receive timeout reset failed");
        relay_state state;
        state.socket = channel.value;
        try {
            child_process child;
            launch_child(child, state, argv[2]);
            std::array<std::byte, 8> hello{};
            wire::put_u32(hello, 0, GetCurrentProcessId());
            wire::put_u32(hello, 4, child.pid);
            if (!state.send_packet(wire::kind::hello, hello)) return 74;
            threads pumps(state, child);
            pumps.start();
            const ULONGLONG deadline = GetTickCount64() + timeout;
            DWORD result = 125;
            bool timed_out = false;
            for (;;) {
                const DWORD wait = WaitForSingleObject(child.process.get(), 50);
                if (wait == WAIT_OBJECT_0) {
                    if (!GetExitCodeProcess(child.process.get(), &result)) win_error("GetExitCodeProcess");
                    break;
                }
                if (wait != WAIT_TIMEOUT) win_error("WaitForSingleObject(child)");
                if (state.stop.load()) break;
                if (GetTickCount64() >= deadline) { timed_out = true; result = 124; break; }
            }
            pumps.join(true);
            std::array<std::byte, 8> exit_info{};
            wire::put_u32(exit_info, 0, result);
            wire::put_u32(exit_info, 4, timed_out ? 2U : (state.stop.load() ? 1U : 0U));
            const bool sent = state.send_packet(wire::kind::exit, exit_info);
            if (state.io_error.load() || !sent) return 74;
            return result == 0 ? 0 : 1;
        } catch (const std::exception& error) {
            const std::string_view message(error.what());
            state.send_packet(wire::kind::error, std::as_bytes(std::span(message.data(), message.size())));
            throw;
        }
    } catch (const std::exception& error) {
        std::fprintf(stderr, "relay: %s\n", error.what());
        return 70;
    }
}
