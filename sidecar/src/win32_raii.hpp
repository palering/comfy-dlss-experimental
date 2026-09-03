#pragma once

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <utility>

namespace comfy_dlss {

class unique_handle final {
public:
    unique_handle() noexcept = default;
    explicit unique_handle(HANDLE handle) noexcept : handle_(handle) {}

    ~unique_handle() {
        reset();
    }

    unique_handle(const unique_handle&) = delete;
    unique_handle& operator=(const unique_handle&) = delete;

    unique_handle(unique_handle&& other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    unique_handle& operator=(unique_handle&& other) noexcept {
        if (this != &other) {
            reset(std::exchange(other.handle_, nullptr));
        }
        return *this;
    }

    [[nodiscard]] HANDLE get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
    }

    void reset(HANDLE replacement = nullptr) noexcept {
        if (handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE) {
            CloseHandle(handle_);
        }
        handle_ = replacement;
    }

private:
    HANDLE handle_ = nullptr;
};

class unique_module final {
public:
    unique_module() noexcept = default;
    explicit unique_module(HMODULE handle) noexcept : handle_(handle) {}

    ~unique_module() {
        reset();
    }

    unique_module(const unique_module&) = delete;
    unique_module& operator=(const unique_module&) = delete;

    unique_module(unique_module&& other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    unique_module& operator=(unique_module&& other) noexcept {
        if (this != &other) {
            reset(std::exchange(other.handle_, nullptr));
        }
        return *this;
    }

    [[nodiscard]] HMODULE get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr;
    }

    [[nodiscard]] FARPROC symbol(const char* name) const noexcept {
        return handle_ ? GetProcAddress(handle_, name) : nullptr;
    }

    void reset(HMODULE replacement = nullptr) noexcept {
        if (handle_) {
            FreeLibrary(handle_);
        }
        handle_ = replacement;
    }

private:
    HMODULE handle_ = nullptr;
};

class unique_window final {
public:
    unique_window() noexcept = default;
    explicit unique_window(HWND handle) noexcept : handle_(handle) {}

    ~unique_window() {
        reset();
    }

    unique_window(const unique_window&) = delete;
    unique_window& operator=(const unique_window&) = delete;

    unique_window(unique_window&& other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    unique_window& operator=(unique_window&& other) noexcept {
        if (this != &other) {
            reset(std::exchange(other.handle_, nullptr));
        }
        return *this;
    }

    [[nodiscard]] HWND get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr;
    }

    void reset(HWND replacement = nullptr) noexcept {
        if (handle_) {
            DestroyWindow(handle_);
        }
        handle_ = replacement;
    }

private:
    HWND handle_ = nullptr;
};

class unique_window_class final {
public:
    unique_window_class() noexcept = default;
    unique_window_class(HINSTANCE instance, ATOM atom, const wchar_t* name) noexcept
        : instance_(instance), atom_(atom), name_(name) {}

    ~unique_window_class() {
        reset();
    }

    unique_window_class(const unique_window_class&) = delete;
    unique_window_class& operator=(const unique_window_class&) = delete;
    unique_window_class(unique_window_class&&) = delete;
    unique_window_class& operator=(unique_window_class&&) = delete;

    [[nodiscard]] explicit operator bool() const noexcept {
        return atom_ != 0;
    }

    void reset() noexcept {
        if (atom_ != 0) {
            UnregisterClassW(name_, instance_);
            atom_ = 0;
        }
    }

private:
    HINSTANCE instance_ = nullptr;
    ATOM atom_ = 0;
    const wchar_t* name_ = nullptr;
};

template <typename Interface>
class com_ptr final {
public:
    com_ptr() noexcept = default;
    explicit com_ptr(Interface* pointer) noexcept : pointer_(pointer) {}

    ~com_ptr() {
        reset();
    }

    com_ptr(const com_ptr&) = delete;
    com_ptr& operator=(const com_ptr&) = delete;

    com_ptr(com_ptr&& other) noexcept
        : pointer_(std::exchange(other.pointer_, nullptr)) {}

    com_ptr& operator=(com_ptr&& other) noexcept {
        if (this != &other) {
            reset(std::exchange(other.pointer_, nullptr));
        }
        return *this;
    }

    [[nodiscard]] Interface* get() const noexcept {
        return pointer_;
    }

    [[nodiscard]] Interface* operator->() const noexcept {
        return pointer_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return pointer_ != nullptr;
    }

    void reset(Interface* replacement = nullptr) noexcept {
        if (pointer_) {
            pointer_->Release();
        }
        pointer_ = replacement;
    }

private:
    Interface* pointer_ = nullptr;
};

}  // namespace comfy_dlss
