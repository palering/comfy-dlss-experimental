#include "nr_parameters.h"
#include <stddef.h>
#include <nvsdk_ngx_params.h>
#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
#endif

// Allocation belongs to a C-boundary host TU. Do not pull MinGW C++/CRT headers
// into the Microsoft-ABI TU or link an MSVC C++ standard library.
extern "C" void* ComfyNR_AllocateParameterStorage(size_t);
extern "C" void ComfyNR_FreeParameterStorage(void*);

// A Windows build MUST use the Microsoft C++ ABI. The same official interface
// has different overload slot order under MinGW. Native builds are tests only.
#if defined(_WIN32) && !defined(_MSC_VER)
#error "Compile nr_parameters.cpp as a Microsoft ABI object, not windows-gnu"
#endif

namespace {
// IEEE-754 binary64 maximum, written without a CRT header so the Microsoft-ABI
// freestanding object and MSVC SDK build use the same NaN/Inf rejection.
constexpr double largest_finite_double = 1.7976931348623157e308;
constexpr uint32_t ok = static_cast<uint32_t>(NVSDK_NGX_Result_Success);
constexpr uint32_t invalid = static_cast<uint32_t>(NVSDK_NGX_Result_FAIL_InvalidParameter);
constexpr auto missing = NVSDK_NGX_Result_FAIL_UnsupportedParameter;
enum class kind { unsigned_integer, signed_integer, real, resource11, resource12, pointer };
struct entry {
    char name[128]{};
    kind type{};
    unsigned long long u = 0;
    int i = 0;
    double real = 0;
    void* pointer = nullptr;
};
class lock_guard {
    bool& flag_;
public:
    explicit lock_guard(bool& flag) noexcept : flag_(flag) {
#if defined(_MSC_VER) && !defined(__clang__)
        // char aliases the bool representation, and only 0/1 are stored.
        // All concurrent accesses use this interlocked operation.
        static_assert(sizeof(bool) == sizeof(char));
        while (_InterlockedExchange8(reinterpret_cast<volatile char*>(&flag_), 1)) {}
#else
        while (__atomic_test_and_set(&flag_, __ATOMIC_ACQUIRE)) {}
#endif
    }
    ~lock_guard() {
#if defined(_MSC_VER) && !defined(__clang__)
        _InterlockedExchange8(reinterpret_cast<volatile char*>(&flag_), 0);
#else
        __atomic_clear(&flag_, __ATOMIC_RELEASE);
#endif
    }
    lock_guard(const lock_guard&) = delete;
    lock_guard& operator=(const lock_guard&) = delete;
};
bool valid_name(const char* name) noexcept {
    if (!name || !name[0]) return false;
    for (unsigned int n = 0; n < 128; ++n) if (!name[n]) return true;
    return false;
}
bool same_name(const char* a, const char* b) noexcept {
    for (unsigned int n = 0; n < 128; ++n) {
        if (a[n] != b[n]) return false;
        if (!a[n]) return true;
    }
    return false;
}
void copy_name(char* to, const char* from) noexcept {
    for (unsigned int n = 0; n < 128; ++n) { to[n] = from[n]; if (!from[n]) return; }
}
} // namespace

struct ComfyNRParameters final : NVSDK_NGX_Parameter {
    static constexpr unsigned int capacity = 192;
    entry entries_[capacity]{};
    unsigned int count_ = 0;
    uint32_t status_ = ok;
    mutable bool lock_ = false;

    static void* operator new(size_t bytes) noexcept { return ComfyNR_AllocateParameterStorage(bytes); }
    static void operator delete(void* pointer) noexcept { ComfyNR_FreeParameterStorage(pointer); }
    ComfyNRParameters() = default;
    ComfyNRParameters(const ComfyNRParameters&) = delete;
    ComfyNRParameters& operator=(const ComfyNRParameters&) = delete;

    const entry* find(const char* name) const noexcept {
        for (unsigned int n = 0; n < count_; ++n)
            if (same_name(entries_[n].name, name)) return &entries_[n];
        return nullptr;
    }
    void put(const char* name, entry value) noexcept {
        lock_guard guard(lock_);
        if (!valid_name(name) || (value.type == kind::real &&
            !(value.real >= -largest_finite_double && value.real <= largest_finite_double))) {
            status_ = invalid; return;
        }
        for (unsigned int n = 0; n < count_; ++n) if (same_name(entries_[n].name, name)) {
            copy_name(value.name, name);
            entries_[n] = value; return;
        }
        if (count_ == capacity) { status_ = invalid; return; }
        copy_name(value.name, name);
        entries_[count_++] = value;
    }
    void Set(const char* n, unsigned long long x) override { entry e; e.type = kind::unsigned_integer; e.u = x; put(n, e); }
    void Set(const char* n, unsigned int x) override { Set(n, static_cast<unsigned long long>(x)); }
    void Set(const char* n, int x) override { entry e; e.type = kind::signed_integer; e.i = x; put(n, e); }
    void Set(const char* n, float x) override { Set(n, static_cast<double>(x)); }
    void Set(const char* n, double x) override { entry e; e.type = kind::real; e.real = x; put(n, e); }
    void Set(const char* n, ID3D11Resource* x) override { entry e; e.type = kind::resource11; e.pointer = x; put(n, e); }
    void Set(const char* n, ID3D12Resource* x) override { entry e; e.type = kind::resource12; e.pointer = x; put(n, e); }
    void Set(const char* n, void* x) override { entry e; e.type = kind::pointer; e.pointer = x; put(n, e); }

    // Numeric conversion is deliberate and bounded; no out-of-range float->int
    // casts, union aliasing, or reinterpretation of numbers as GPU pointers.
    static bool convert(const entry& e, unsigned long long& out) noexcept {
        if (e.type == kind::unsigned_integer) { out = e.u; return true; }
        if (e.type == kind::signed_integer && e.i >= 0) { out = static_cast<unsigned long long>(e.i); return true; }
        if (e.type == kind::real && e.real >= 0 && e.real < 18446744073709551616.0) {
            const auto value = static_cast<unsigned long long>(e.real);
            if (static_cast<double>(value) == e.real) { out = value; return true; }
        }
        return false;
    }
    static bool convert(const entry& e, unsigned int& out) noexcept {
        unsigned long long x = 0;
        if (!convert(e, x) || x > 0xffffffffULL) return false;
        out = static_cast<unsigned int>(x); return true;
    }
    static bool convert(const entry& e, int& out) noexcept {
        if (e.type == kind::signed_integer) { out = e.i; return true; }
        if (e.type == kind::unsigned_integer && e.u <= 0x7fffffffULL) { out = static_cast<int>(e.u); return true; }
        if (e.type == kind::real && e.real >= -2147483648.0 && e.real < 2147483648.0) {
            const int x = static_cast<int>(e.real);
            if (static_cast<double>(x) == e.real) { out = x; return true; }
        }
        return false;
    }
    static bool convert(const entry& e, double& out) noexcept {
        if (e.type == kind::unsigned_integer) out = static_cast<double>(e.u);
        else if (e.type == kind::signed_integer) out = e.i;
        else if (e.type == kind::real) out = e.real;
        else return false;
        return true;
    }
    static bool convert(const entry& e, float& out) noexcept {
        double x = 0;
        constexpr double float_max = 0x1.fffffep127;
        if (!convert(e, x) || x > float_max || x < -float_max) return false;
        out = static_cast<float>(x); return true;
    }
    static bool convert(const entry& e, void*& out) noexcept {
        if (e.type != kind::pointer && e.type != kind::resource11 && e.type != kind::resource12) return false;
        out = e.pointer; return true;
    }
    static bool convert(const entry& e, ID3D11Resource*& out) noexcept {
        if (e.type != kind::resource11 && e.type != kind::pointer) return false;
        out = static_cast<ID3D11Resource*>(e.pointer); return true;
    }
    static bool convert(const entry& e, ID3D12Resource*& out) noexcept {
        if (e.type != kind::resource12 && e.type != kind::pointer) return false;
        out = static_cast<ID3D12Resource*>(e.pointer); return true;
    }
    template<class T> NVSDK_NGX_Result get(const char* name, T* output) const noexcept {
        if (!output || !valid_name(name)) return NVSDK_NGX_Result_FAIL_InvalidParameter;
        lock_guard guard(lock_);
        const entry* e = find(name);
        if (!e) return missing;
        T value{};
        if (!convert(*e, value)) return NVSDK_NGX_Result_FAIL_InvalidParameter;
        *output = value; return NVSDK_NGX_Result_Success;
    }
#define GET(T) NVSDK_NGX_Result Get(const char* name, T* output) const override { return get(name, output); }
    GET(unsigned long long) GET(float) GET(double) GET(unsigned int) GET(int)
    GET(ID3D11Resource*) GET(ID3D12Resource*) GET(void*)
#undef GET
    void Reset() override { lock_guard guard(lock_); count_ = 0; status_ = ok; }
    uint32_t status() const noexcept { lock_guard guard(lock_); return status_; }
};

extern "C" {
ComfyNRParameters* ComfyNR_ParametersCreate() { return new ComfyNRParameters; }
void ComfyNR_ParametersDestroy(ComfyNRParameters* p) { delete p; }
void* ComfyNR_ParameterInterface(ComfyNRParameters* p) { return static_cast<NVSDK_NGX_Parameter*>(p); }
uint32_t ComfyNR_ParametersStatus(const ComfyNRParameters* p) { return p ? p->status() : invalid; }
void ComfyNR_ParametersReset(ComfyNRParameters* p) { if (p) p->Reset(); }
void ComfyNR_SetU32(ComfyNRParameters* p, const char* n, uint32_t v) { if (p) p->Set(n, static_cast<unsigned int>(v)); }
void ComfyNR_SetI32(ComfyNRParameters* p, const char* n, int32_t v) { if (p) p->Set(n, static_cast<int>(v)); }
void ComfyNR_SetU64(ComfyNRParameters* p, const char* n, uint64_t v) { if (p) p->Set(n, static_cast<unsigned long long>(v)); }
void ComfyNR_SetF32(ComfyNRParameters* p, const char* n, float v) { if (p) p->Set(n, v); }
void ComfyNR_SetF64(ComfyNRParameters* p, const char* n, double v) { if (p) p->Set(n, v); }
void ComfyNR_SetResource12(ComfyNRParameters* p, const char* n, void* v) { if (p) p->Set(n, static_cast<ID3D12Resource*>(v)); }
void ComfyNR_SetPointer(ComfyNRParameters* p, const char* n, void* v) { if (p) p->Set(n, v); }
}
